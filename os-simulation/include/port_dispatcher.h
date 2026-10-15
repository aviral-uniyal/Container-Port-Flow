#ifndef PORT_DISPATCHER_H
#define PORT_DISPATCHER_H

#include "resource_manager.h"    // ResourceManager, ResourceSet, ResourceRequest
#include "crane_registry.h"      // CraneRegistry, CraneStatus, CraneState
#include "disruption_manager.h"  // DisruptionManager, JobProgress, DisruptionResult

#include <string>
#include <vector>
#include <deque>
#include <unordered_map>
#include <unordered_set>
#include <stdexcept>

// ─── Scheduling policy ────────────────────────────────────────────────────────
// Determines queue ordering when selecting the next job to dispatch.
//
//   FCFS     – first-come first-served (submission_seq ordering)
//   SJF      – shortest job first (work_remaining ascending; seq tiebreak)
//   PRIORITY – lowest priority number first (seq tiebreak)
//
// Policy is fixed at PortDispatcher construction and applied every time the
// dispatcher selects the next job from the pending queue.

enum class DispatchPolicy {
    FCFS,
    SJF,
    PRIORITY
};

// ─── Port job descriptor ──────────────────────────────────────────────────────
// Submitted by the caller when enqueuing a new job (vessel operation).

struct PortJob {
    std::string job_id;
    int arrival_time;   // >= 0
    int work_total;     // > 0
    int priority;       // lower value == higher priority
    int berths;         // >= 0
    int cranes;         // > 0
    int trucks;         // >= 0
};

// ─── Job lifecycle state ──────────────────────────────────────────────────────
//
//   PENDING     – queued, waiting for resources / crane assignment
//   ACTIVE      – resources allocated; at least one crane currently assigned
//                 Note: an ACTIVE job may be "undersupplied" — it has fewer
//                 active_cranes than job.cranes requires — if one crane failed
//                 while at least one other remains.  Such a job cannot be
//                 completed until the missing cranes are reassigned.
//   INTERRUPTED – last assigned crane failed; no working crane remains;
//                 RM allocation retained; re-queued for crane reassignment
//   COMPLETED   – all work done; RM resources released
//   CANCELLED   – explicitly cancelled; RM resources released

enum class JobState {
    PENDING,
    ACTIVE,
    INTERRUPTED,
    COMPLETED,
    CANCELLED
};

// ─── Live job record ──────────────────────────────────────────────────────────

struct JobRecord {
    PortJob     job;            // original descriptor (immutable after submit)
    JobState    state;
    int         work_remaining; // starts == job.work_total; decremented on partial completion
    int         work_completed; // starts 0
    std::unordered_set<std::string> active_cranes;  // currently ASSIGNED crane IDs
    int         submission_seq; // monotonic counter for FCFS ordering
    bool        rm_allocated;   // true iff ResourceManager holds an allocation for this job
};

// ─── Event result: crane failure ──────────────────────────────────────────────

struct FailureResult {
    std::string crane_id;
    std::string affected_job_id; // "" if crane was idle
    bool        job_interrupted; // true iff job lost all cranes and was re-queued
    bool        dispatched_next; // true iff a pending job got a crane in this call
                                 // (only possible when idle crane fails and a
                                 //  pending job is immediately dispatchable)
};

// ─── Event result: crane recovery ─────────────────────────────────────────────

struct RecoveryResult {
    std::string crane_id;
    std::string dispatched_job_id; // "" if nothing was dispatched
    bool        job_dispatched;
};

// ═════════════════════════════════════════════════════════════════════════════
// PortDispatcher
// ═════════════════════════════════════════════════════════════════════════════
//
// Integration boundary
// ────────────────────
// The existing schedulers (schedule_fcfs / schedule_sjf / schedule_priority in
// scheduler.h) are *one-shot batch algorithms*: they consume a complete list of
// processes, run the algorithm to completion, and return static timing tables.
// They have no hooks for mid-execution crane failures or dynamic re-queuing.
//
// PortDispatcher is the *smallest additional layer* that:
//   • Applies the same ordering criterion (FCFS / SJF / Priority) to select
//     the next job from a live queue, consistent with the batch algorithms.
//   • Integrates crane failure/recovery events from DisruptionManager.
//   • Maintains correct resource accounting through ResourceManager and
//     CraneRegistry without double-releasing or fabricating capacity.
//
// Resource-accounting invariant
// ──────────────────────────────
// For every ACTIVE job J with allocation (b, c, t):
//   • rm_.get_job_allocation(J.job_id) == {b, c, t}   [aggregate RM view]
//   • Every crane in J.active_cranes has status ASSIGNED in CraneRegistry.
//
// When a single crane fails while the job still has other assigned cranes:
//   • CraneRegistry marks the crane FAILED.
//   • J.active_cranes loses that crane ID.
//   • ResourceManager allocation is unchanged (berths + trucks remain; the
//     crane count in RM stays at the original requested quantity).
//
// When the job's last crane fails:
//   • Same CraneRegistry / active_cranes changes as above.
//   • Job transitions ACTIVE → INTERRUPTED and is re-queued.
//   • ResourceManager allocation is still unchanged (berths/trucks stay locked).
//
// RM release happens EXACTLY ONCE per job, at complete_job() or cancel_job().
//
// INTERRUPTED re-dispatch
// ────────────────────────
// An INTERRUPTED job re-enters the queue with its work_remaining preserved.
// On next dispatch the job is given available cranes (at least one, up to its
// required count); it resumes from where it left off.  Its RM allocation
// (berths/trucks) is already held, so only a CraneRegistry assign_crane() call
// is needed — no new RM allocation.

class PortDispatcher {
public:
    // Construct with resource capacities and scheduling policy.
    // Throws std::invalid_argument on negative capacities.
    PortDispatcher(int total_berths,
                   int total_cranes,
                   int total_trucks,
                   DispatchPolicy policy = DispatchPolicy::FCFS);

    // ── Crane registration ─────────────────────────────────────────────────────

    // Register a crane with the internal CraneRegistry.
    // Must be called before jobs requiring cranes can be dispatched.
    // Throws std::invalid_argument on duplicate or empty crane_id.
    void register_crane(const std::string& crane_id);

    // ── Job submission ─────────────────────────────────────────────────────────

    // Enqueue a new job.  If resources are immediately available the job is
    // dispatched (PENDING -> ACTIVE) synchronously within this call.
    //
    // Throws std::invalid_argument on:
    //   • empty job_id
    //   • duplicate job_id
    //   • arrival_time < 0
    //   • work_total <= 0
    //   • cranes <= 0
    //   • berths < 0 or trucks < 0
    void submit_job(const PortJob& job);

    // ── Crane event handlers ───────────────────────────────────────────────────

    // Report that an ASSIGNED crane has failed mid-job.
    //
    // Parameters:
    //   crane_id       – must be ASSIGNED
    //   work_completed – units completed by this crane (>= 0)
    //   work_remaining – units still needed (> 0)
    //
    // Behaviour:
    //   1. Delegates to DisruptionManager::fail_assigned_crane() to capture
    //      JobProgress and mark the crane FAILED.
    //   2. Removes the crane from the job's active_cranes set.
    //   3. Accumulates partial progress into the job record.
    //   4. If the job still has other assigned cranes: job stays ACTIVE.
    //   5. If the job has NO remaining cranes:
    //        a. Transitions job ACTIVE -> INTERRUPTED.
    //        b. Re-enqueues the job (preserving work_remaining).
    //   6. ResourceManager allocation is NOT touched.
    //
    // Throws std::invalid_argument on:
    //   • empty crane_id / crane not ASSIGNED
    //   • work_completed < 0 / work_remaining <= 0
    FailureResult on_crane_failed(const std::string& crane_id,
                                  int work_completed,
                                  int work_remaining);

    // Report that an AVAILABLE (idle) crane has failed.
    //
    // No job is interrupted; no queue changes occur.
    //
    // Throws std::invalid_argument on:
    //   • empty crane_id / crane not AVAILABLE
    FailureResult on_idle_crane_failed(const std::string& crane_id);

    // Report that a FAILED crane has been repaired (FAILED -> AVAILABLE).
    //
    // Behaviour:
    //   1. Delegates to DisruptionManager::recover_crane().
    //   2. Attempts to dispatch the highest-priority pending/interrupted job.
    //
    // Throws std::invalid_argument on:
    //   • empty crane_id / crane not FAILED
    RecoveryResult on_crane_recovered(const std::string& crane_id);

    // ── Job lifecycle ──────────────────────────────────────────────────────────

    // Mark an ACTIVE job complete.
    // Releases RM allocation and releases all assigned cranes to AVAILABLE.
    //
    // Completion is only permitted when the job has ALL required cranes
    // currently ASSIGNED (active_cranes.size() == job.cranes).  If any
    // required crane is missing (crane failed but the job was not yet
    // interrupted because other cranes remained), the call is rejected.
    //
    // Throws std::invalid_argument on:
    //   • empty job_id / unknown job_id / job not ACTIVE
    //   • job is ACTIVE but has fewer active cranes than job.cranes requires
    void complete_job(const std::string& job_id);

    // Returns true iff a job has all of its required cranes currently ASSIGNED
    // (i.e., active_cranes.size() == job.cranes).  A job may be ACTIVE but
    // still be undersupplied if a crane failed while other cranes remained.
    bool crane_count_fulfilled(const std::string& job_id) const;

    // Cancel a job (PENDING, ACTIVE, or INTERRUPTED -> CANCELLED).
    // Releases RM allocation (if any) and releases all assigned cranes.
    //
    // Throws std::invalid_argument on:
    //   • empty job_id / unknown job_id
    //   • job already COMPLETED or CANCELLED
    void cancel_job(const std::string& job_id);

    // ── Queries ────────────────────────────────────────────────────────────────

    // Current state of a submitted job.
    // Throws std::invalid_argument if unknown.
    JobState job_state(const std::string& job_id) const;

    // Full live record for a job.
    // Throws std::invalid_argument if unknown.
    const JobRecord& job_record(const std::string& job_id) const;

    // Number of jobs in PENDING or INTERRUPTED state (in the dispatch queue).
    int pending_count() const;

    // Number of jobs in ACTIVE state.
    int active_count() const;

    // Read-only access to underlying managers (for testing/inspection).
    const ResourceManager&   resource_manager()    const { return rm_; }
    const CraneRegistry&     crane_registry()      const { return registry_; }
    const DisruptionManager& disruption_manager()  const { return dm_; }

private:
    ResourceManager   rm_;
    CraneRegistry     registry_;
    DisruptionManager dm_;
    DispatchPolicy    policy_;

    int next_seq_ = 0;  // monotonic submission counter

    // All known job records, keyed by job_id.
    std::unordered_map<std::string, JobRecord> job_records_;

    // Ordered queue of pending/interrupted job IDs.
    std::deque<std::string> pending_queue_;

    // ── Internal helpers ───────────────────────────────────────────────────────

    JobRecord&       find_record(const std::string& job_id);
    const JobRecord& find_record(const std::string& job_id) const;

    void validate_port_job(const PortJob& job) const;

    // Try to dispatch an INTERRUPTED job: assign available cranes (at least 1,
    // up to the job's required count) and transition it back to ACTIVE.
    // Returns dispatched job_id or "".
    std::string try_dispatch_interrupted();

    // Try to dispatch a PENDING job: allocate RM resources + assign cranes.
    // Returns dispatched job_id or "".
    std::string try_dispatch_pending();

    // Main dispatch pump: tries interrupted first (to avoid starvation),
    // then pending.  Returns dispatched job_id or "".
    std::string try_dispatch();

    // Top-up step: for every ACTIVE job that is undersupplied (has fewer
    // active_cranes than job.cranes), assign as many available cranes as
    // possible up to the required count.  Called after crane recovery so
    // that a job which stayed ACTIVE (because other cranes remained) can
    // reclaim its lost cranes without going through INTERRUPTED.
    // Returns the job_id of the first job topped up, or "" if none.
    std::string try_fill_undersupplied_active();

    // Sort the pending queue according to the active policy.
    void sort_queue();

    // Release RM + CraneRegistry resources for a job.
    void release_job_resources(JobRecord& rec);

    // Remove a job from the pending_queue_ (if present).
    void remove_from_queue(const std::string& job_id);
};

#endif // PORT_DISPATCHER_H
