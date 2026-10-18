#include "port_dispatcher.h"

#include <algorithm>
#include <stdexcept>
#include <vector>

// ─── Construction ─────────────────────────────────────────────────────────────

PortDispatcher::PortDispatcher(int total_berths,
                               int total_cranes,
                               int total_trucks,
                               DispatchPolicy policy)
    : rm_(total_berths, total_cranes, total_trucks),
      registry_(),
      dm_(registry_),
      policy_(policy),
      next_seq_(0),
      next_dispatch_seq_(0)
{
    // ResourceManager constructor already validates negative capacities.
}

// ─── Crane registration ────────────────────────────────────────────────────────

void PortDispatcher::register_crane(const std::string& crane_id) {
    registry_.register_crane(crane_id);  // throws on duplicate / empty
}

// ─── Internal helpers ──────────────────────────────────────────────────────────

JobRecord& PortDispatcher::find_record(const std::string& job_id) {
    auto it = job_records_.find(job_id);
    if (it == job_records_.end()) {
        throw std::invalid_argument("Unknown job_id: " + job_id);
    }
    return it->second;
}

const JobRecord& PortDispatcher::find_record(const std::string& job_id) const {
    auto it = job_records_.find(job_id);
    if (it == job_records_.end()) {
        throw std::invalid_argument("Unknown job_id: " + job_id);
    }
    return it->second;
}

void PortDispatcher::validate_port_job(const PortJob& job) const {
    if (job.job_id.empty()) {
        throw std::invalid_argument("PortJob: job_id must not be empty.");
    }
    if (job_records_.count(job.job_id)) {
        throw std::invalid_argument("PortJob: duplicate job_id: " + job.job_id);
    }
    if (job.arrival_time < 0) {
        throw std::invalid_argument("PortJob: arrival_time must be >= 0 for job " + job.job_id);
    }
    if (job.work_total <= 0) {
        throw std::invalid_argument("PortJob: work_total must be > 0 for job " + job.job_id);
    }
    if (job.cranes <= 0) {
        throw std::invalid_argument("PortJob: cranes must be > 0 for job " + job.job_id);
    }
    if (job.berths < 0) {
        throw std::invalid_argument("PortJob: berths must be >= 0 for job " + job.job_id);
    }
    if (job.trucks < 0) {
        throw std::invalid_argument("PortJob: trucks must be >= 0 for job " + job.job_id);
    }
    ResourceSet total = rm_.total_capacity();
    if (job.berths > total.berths) {
        throw std::invalid_argument("PortJob: requested berths (" + std::to_string(job.berths) +
                                    ") exceeds total capacity (" + std::to_string(total.berths) + ")");
    }
    if (job.cranes > total.cranes) {
        throw std::invalid_argument("PortJob: requested cranes (" + std::to_string(job.cranes) +
                                    ") exceeds total capacity (" + std::to_string(total.cranes) + ")");
    }
    if (job.trucks > total.trucks) {
        throw std::invalid_argument("PortJob: requested trucks (" + std::to_string(job.trucks) +
                                    ") exceeds total capacity (" + std::to_string(total.trucks) + ")");
    }
}

void PortDispatcher::remove_from_queue(const std::string& job_id) {
    auto it = std::find(pending_queue_.begin(), pending_queue_.end(), job_id);
    if (it != pending_queue_.end()) {
        pending_queue_.erase(it);
    }
}

// ─── Queue sorting ─────────────────────────────────────────────────────────────
// Sorts the pending queue so that index 0 is the highest-priority job.
// INTERRUPTED jobs are given precedence over PENDING jobs with the same key to
// avoid starvation (they already hold RM resources).

bool PortDispatcher::queue_precedes(const std::string& a,
                                    const std::string& b) const {
    const JobRecord& ra = job_records_.at(a);
    const JobRecord& rb = job_records_.at(b);

    // INTERRUPTED before PENDING (same logical priority, same key).
    bool a_interrupted = (ra.state == JobState::INTERRUPTED);
    bool b_interrupted = (rb.state == JobState::INTERRUPTED);

    switch (policy_) {
        case DispatchPolicy::FCFS:
            // Primary: interrupted first.  Secondary: submission order.
            if (a_interrupted != b_interrupted) return a_interrupted > b_interrupted;
            return ra.submission_seq < rb.submission_seq;

        case DispatchPolicy::SJF:
            // Primary: interrupted first.  Secondary: shorter remaining work.
            // Tiebreak: submission order.
            if (a_interrupted != b_interrupted) return a_interrupted > b_interrupted;
            if (ra.work_remaining != rb.work_remaining)
                return ra.work_remaining < rb.work_remaining;
            return ra.submission_seq < rb.submission_seq;

        case DispatchPolicy::PRIORITY:
            // Primary: interrupted first.  Secondary: lower priority number.
            // Tiebreak: submission order.
            if (a_interrupted != b_interrupted) return a_interrupted > b_interrupted;
            if (ra.job.priority != rb.job.priority)
                return ra.job.priority < rb.job.priority;
            return ra.submission_seq < rb.submission_seq;
    }
    return false; // unreachable
}

void PortDispatcher::sort_queue() {
    std::stable_sort(pending_queue_.begin(), pending_queue_.end(),
        [this](const std::string& a, const std::string& b) {
            return queue_precedes(a, b);
        });
}

// ─── Release job resources ────────────────────────────────────────────────────
// Common path for complete_job() and cancel_job().
// Releases:
//   1. All currently ASSIGNED cranes for this job (CraneRegistry).
//   2. The RM allocation (ResourceManager) — once, if rm_allocated is true.
// Does NOT touch FAILED cranes (they remain FAILED until recovered externally).

void PortDispatcher::release_job_resources(JobRecord& rec) {
    // Release assigned cranes.
    for (const auto& cid : rec.active_cranes) {
        // The crane should be ASSIGNED at this point.  release_crane() would
        // throw if it is somehow not ASSIGNED, which is a programming error
        // we want to surface rather than silently ignore.
        registry_.release_crane(cid);
    }
    rec.active_cranes.clear();

    // Release RM allocation (once, if held).
    if (rec.rm_allocated) {
        rm_.release(rec.job.job_id);
        rec.rm_allocated = false;
    }
}

// ─── Dispatch: INTERRUPTED job ────────────────────────────────────────────────
// Assigns one available crane to the first INTERRUPTED job in the sorted queue.
// The RM allocation is already held by the interrupted job; no new RM call needed.

std::string PortDispatcher::try_dispatch_interrupted() {
    auto avail = registry_.available_cranes();
    if (avail.empty()) return "";
    std::sort(avail.begin(), avail.end());

    for (size_t qi = 0; qi < pending_queue_.size(); ++qi) {
        const std::string& jid = pending_queue_[qi];
        JobRecord& rec = job_records_.at(jid);
        if (rec.state != JobState::INTERRUPTED) continue;

        // Assign available cranes (at least 1, up to the job's original requested count).
        int to_assign = std::min(static_cast<int>(avail.size()), rec.job.cranes);
        for (int k = 0; k < to_assign; ++k) {
            const std::string& crane_id = avail[static_cast<size_t>(k)];
            registry_.assign_crane(crane_id, jid);
            rec.active_cranes.insert(crane_id);
        }
        rec.state = JobState::ACTIVE;
        if (rec.dispatch_seq < 0) {
            rec.dispatch_seq = next_dispatch_seq_++;
        }

        // Remove from pending queue.
        pending_queue_.erase(pending_queue_.begin() + static_cast<int>(qi));
        return jid;
    }
    return "";
}

// ─── Dispatch: PENDING job ────────────────────────────────────────────────────
// Tries to allocate RM resources and assign cranes for the first PENDING job
// in the sorted queue whose resource requirements can be fully met.

std::string PortDispatcher::try_dispatch_pending() {
    for (size_t qi = 0; qi < pending_queue_.size(); ++qi) {
        const std::string& jid = pending_queue_[qi];
        JobRecord& rec = job_records_.at(jid);
        if (rec.state != JobState::PENDING) continue;

        const PortJob& job = rec.job;

        // Check RM availability without committing.
        ResourceRequest req{job.job_id, job.berths, job.cranes, job.trucks};
        if (!rm_.can_allocate(req)) continue;

        // Check that enough individual cranes are available in CraneRegistry.
        auto avail = registry_.available_cranes();
        if (static_cast<int>(avail.size()) < job.cranes) continue;
        std::sort(avail.begin(), avail.end());

        // Commit RM allocation.
        bool ok = rm_.allocate(req);
        if (!ok) continue;   // race: couldn't allocate (shouldn't happen here)

        // Assign exactly job.cranes cranes from the available list.
        for (int k = 0; k < job.cranes; ++k) {
            registry_.assign_crane(avail[static_cast<size_t>(k)], job.job_id);
            rec.active_cranes.insert(avail[static_cast<size_t>(k)]);
        }

        rec.state        = JobState::ACTIVE;
        if (rec.dispatch_seq < 0) {
            rec.dispatch_seq = next_dispatch_seq_++;
        }
        rec.rm_allocated = true;

        // Remove from pending queue.
        pending_queue_.erase(pending_queue_.begin() + static_cast<int>(qi));
        return jid;
    }
    return "";
}

// ─── Main dispatch pump ───────────────────────────────────────────────────────
// Priority: interrupted jobs first (they already hold RM resources; unblocking
// them is cheaper and avoids resource deadlock), then pending jobs.

std::string PortDispatcher::try_dispatch() {
    sort_queue();
    std::string dispatched = try_dispatch_interrupted();
    if (!dispatched.empty()) return dispatched;
    if (auto_dispatch_) return try_dispatch_pending();
    return "";
}

// ─── Batch-dispatch mode ───────────────────────────────────────────────────────

void PortDispatcher::set_auto_dispatch(bool enabled) {
    auto_dispatch_ = enabled;
}

// Drain the queue: repeatedly tries to dispatch (both interrupted and pending)
// until no job can be dispatched.  Returns the IDs of all jobs dispatched.
//
// Termination: each successful iteration removes exactly one entry from
// pending_queue_.  The loop therefore runs at most N times where N is the
// queue depth at entry.  try_dispatch_pending() returns "" immediately when
// resources are exhausted or the queue is empty.
std::vector<std::string> PortDispatcher::flush_dispatch() {
    std::vector<std::string> dispatched;
    while (true) {
        sort_queue();
        std::string jid = try_dispatch_interrupted();
        if (jid.empty()) jid = try_dispatch_pending();
        if (jid.empty()) break;
        dispatched.push_back(jid);
    }
    return dispatched;
}

// ─── Job submission ────────────────────────────────────────────────────────────

void PortDispatcher::submit_job(const PortJob& job) {
    validate_port_job(job);  // throws on invalid

    JobRecord rec;
    rec.job            = job;
    rec.state          = JobState::PENDING;
    rec.work_remaining = job.work_total;
    rec.work_completed = 0;
    rec.submission_seq = next_seq_++;
    rec.dispatch_seq   = -1;
    rec.rm_allocated   = false;

    job_records_.emplace(job.job_id, std::move(rec));
    pending_queue_.push_back(job.job_id);

    // Try to dispatch immediately.
    try_dispatch();
}

// ─── Crane failure: assigned ──────────────────────────────────────────────────

FailureResult PortDispatcher::on_crane_failed(const std::string& crane_id,
                                               int work_completed,
                                               int work_remaining)
{
    // Validate the legacy progress report before mutating the crane registry.
    // The API historically supplied either:
    //   * absolute job progress (completed + remaining == total), or
    //   * progress since the previous failure
    //     (completed + remaining == the dispatcher's current remaining work).
    // Supporting both forms preserves compatibility while preventing a
    // cumulative snapshot value from being added twice after step() has run.
    if (work_completed < 0 || work_remaining <= 0) {
        throw std::invalid_argument(
            "on_crane_failed: invalid work progress for crane " + crane_id);
    }
    CraneState crane = registry_.get_crane(crane_id);
    if (crane.status != CraneStatus::ASSIGNED) {
        // Preserve DisruptionManager's existing status-specific errors.
        dm_.fail_assigned_crane(crane_id, work_completed, work_remaining);
    }

    JobRecord& rec = find_record(crane.assigned_job);
    const bool absolute_report =
        (work_completed + work_remaining == rec.job.work_total);
    const bool incremental_report =
        (work_completed + work_remaining == rec.work_remaining);

    int next_completed = rec.work_completed;
    int next_remaining = rec.work_remaining;
    if (absolute_report) {
        if (work_completed < rec.work_completed ||
            work_remaining > rec.work_remaining) {
            throw std::invalid_argument(
                "on_crane_failed: stale absolute work progress for job " +
                rec.job.job_id);
        }
        next_completed = work_completed;
        next_remaining = work_remaining;
    } else if (incremental_report) {
        next_completed += work_completed;
        next_remaining = work_remaining;
    } else {
        throw std::invalid_argument(
            "on_crane_failed: progress does not match job work total for job " +
            rec.job.job_id);
    }

    if (next_completed + next_remaining != rec.job.work_total) {
        throw std::invalid_argument(
            "on_crane_failed: work progress invariant violated for job " +
            rec.job.job_id);
    }

    // Delegate only after all dispatcher-side validation has succeeded.
    // DisruptionResult.affected_job contains the job_id.
    DisruptionResult dr = dm_.fail_assigned_crane(crane_id, work_completed, work_remaining);
    const std::string& job_id = dr.affected_job.job_id;

    // Update the job record.
    rec.active_cranes.erase(crane_id);

    // Apply the normalized absolute progress calculated above.
    rec.work_completed = next_completed;
    rec.work_remaining = next_remaining;

    FailureResult fr;
    fr.crane_id        = crane_id;
    fr.affected_job_id = job_id;
    fr.job_interrupted = false;
    fr.dispatched_next = false;

    if (rec.active_cranes.empty()) {
        // No working cranes remain — interrupt the job.
        rec.state = JobState::INTERRUPTED;
        pending_queue_.push_front(job_id);
        fr.job_interrupted = true;
        // Do NOT attempt to dispatch here: the crane that just failed is FAILED,
        // so it cannot immediately serve another job.  Recovery is needed first.
    }
    // If other cranes remain, job stays ACTIVE; nothing else to do.

    return fr;
}

// ─── Crane failure: idle ──────────────────────────────────────────────────────

FailureResult PortDispatcher::on_idle_crane_failed(const std::string& crane_id) {
    // DisruptionManager validates and marks the crane FAILED.
    dm_.fail_idle_crane(crane_id);

    FailureResult fr;
    fr.crane_id        = crane_id;
    fr.affected_job_id = "";
    fr.job_interrupted = false;
    fr.dispatched_next = false;
    // An idle crane failing does not free any resources nor trigger dispatch.
    return fr;
}

// ─── Crane recovery ───────────────────────────────────────────────────────────

RecoveryResult PortDispatcher::on_crane_recovered(const std::string& crane_id) {
    // Recover the crane (FAILED -> AVAILABLE).
    dm_.recover_crane(crane_id);

    RecoveryResult rr;
    rr.crane_id        = crane_id;
    rr.dispatched_job_id = "";
    rr.job_dispatched  = false;

    // Priority 1: Fill an ACTIVE job that is undersupplied (crane failed while
    // other cranes remained, so the job never became INTERRUPTED).
    std::string topped_up = try_fill_undersupplied_active();
    if (!topped_up.empty()) {
        rr.dispatched_job_id = topped_up;
        rr.job_dispatched    = true;
        return rr;
    }

    // Priority 2: Re-dispatch an INTERRUPTED job (already holds RM resources;
    // deferring it would leave those resources stranded indefinitely).
    // This is resource recovery, not a new dispatch, so it runs regardless of
    // auto_dispatch_.  Fresh PENDING jobs are only dispatched here when
    // auto_dispatch_ is true.
    sort_queue();
    std::string dispatched = try_dispatch_interrupted();
    if (dispatched.empty() && auto_dispatch_) {
        dispatched = try_dispatch_pending();
    }
    if (!dispatched.empty()) {
        rr.dispatched_job_id = dispatched;
        rr.job_dispatched    = true;
    }

    return rr;
}

// ─── Top-up: fill undersupplied ACTIVE jobs ──────────────────────────────────────
// Scans ACTIVE jobs in submission order and assigns available cranes to those
// that have fewer active_cranes than job.cranes requires.  The RM allocation
// for such a job is already fully held, so no new RM call is needed.

std::string PortDispatcher::try_fill_undersupplied_active() {
    auto avail = registry_.available_cranes();
    if (avail.empty()) return "";
    std::sort(avail.begin(), avail.end());

    // Collect undersupplied ACTIVE jobs sorted by submission_seq.
    std::vector<std::pair<int, std::string>> undersupplied;
    for (const auto& kv : job_records_) {
        if (kv.second.state == JobState::ACTIVE) {
            const JobRecord& rec = kv.second;
            int missing = rec.job.cranes - static_cast<int>(rec.active_cranes.size());
            if (missing > 0) {
                undersupplied.push_back({rec.submission_seq, kv.first});
            }
        }
    }
    if (undersupplied.empty()) return "";
    std::sort(undersupplied.begin(), undersupplied.end());

    std::string first_topped;
    size_t avail_idx = 0;
    for (const auto& p : undersupplied) {
        if (avail_idx >= avail.size()) break;
        JobRecord& rec = job_records_.at(p.second);
        int missing = rec.job.cranes - static_cast<int>(rec.active_cranes.size());
        for (int i = 0; i < missing && avail_idx < avail.size(); ++i, ++avail_idx) {
            const std::string& cid = avail[avail_idx];
            registry_.assign_crane(cid, rec.job.job_id);
            rec.active_cranes.insert(cid);
        }
        if (first_topped.empty()) first_topped = p.second;
    }
    return first_topped;
}

// ─── Complete job ─────────────────────────────────────────────────────────────

void PortDispatcher::complete_job(const std::string& job_id) {
    if (job_id.empty()) {
        throw std::invalid_argument("complete_job: job_id must not be empty.");
    }
    JobRecord& rec = find_record(job_id);
    if (rec.state != JobState::ACTIVE) {
        throw std::invalid_argument(
            "complete_job: job " + job_id + " is not ACTIVE (state = " +
            std::to_string(static_cast<int>(rec.state)) + ").");
    }

    // Enforce full crane allocation before allowing completion.
    // An ACTIVE job can be undersupplied when a crane failed but at least one
    // other crane kept the job running.  Premature completion is rejected so
    // the caller cannot bypass the resource-recovery workflow.
    int have = static_cast<int>(rec.active_cranes.size());
    int need = rec.job.cranes;
    if (have < need) {
        throw std::invalid_argument(
            "complete_job: job " + job_id + " requires " +
            std::to_string(need) + " crane(s) but only " +
            std::to_string(have) + " are currently ASSIGNED. " +
            "Recover the missing crane(s) before completing.");
    }

    release_job_resources(rec);
    rec.state = JobState::COMPLETED;
    rec.work_completed = rec.job.work_total;
    rec.work_remaining = 0;

    // Attempt to dispatch the next waiting job with the freed resources.
    try_dispatch();
}

StepResult PortDispatcher::step(bool continue_batch) {
    StepResult result;

    // Capture ACTIVE jobs before any completion can dispatch replacements.
    // Sorting makes both processing and response order deterministic despite
    // job_records_ being an unordered_map.
    std::vector<std::pair<int, std::string>> active_at_start;
    std::unordered_set<std::string> active_ids_at_start;
    for (const auto& kv : job_records_) {
        if (kv.second.state == JobState::ACTIVE) {
            active_at_start.push_back({kv.second.submission_seq, kv.first});
            active_ids_at_start.insert(kv.first);
        }
    }
    std::sort(active_at_start.begin(), active_at_start.end());

    std::vector<std::string> reached_zero;
    for (const auto& entry : active_at_start) {
        JobRecord& rec = job_records_.at(entry.second);

        // A partially staffed ACTIVE job pauses until recovery tops it up.
        if (static_cast<int>(rec.active_cranes.size()) < rec.job.cranes) {
            continue;
        }
        if (rec.work_remaining <= 0) {
            continue;
        }

        --rec.work_remaining;
        rec.work_completed = rec.job.work_total - rec.work_remaining;
        result.advanced_job_ids.push_back(rec.job.job_id);

        if (rec.work_remaining == 0) {
            reached_zero.push_back(rec.job.job_id);
        }
    }

    // Reuse the existing completion path so cranes and aggregate resources are
    // released exactly once and immediate-mode dispatch remains unchanged.
    for (const std::string& job_id : reached_zero) {
        complete_job(job_id);
        result.completed_job_ids.push_back(job_id);
    }

    // Per-operation authorization for a future Run controller. A normal step
    // leaves fresh batch jobs queued; continue_batch explicitly drains all jobs
    // that fit after this step's work and completions.
    if (continue_batch && !auto_dispatch_) {
        flush_dispatch();
    }

    std::vector<std::pair<int, std::string>> newly_active;
    for (const auto& kv : job_records_) {
        if (kv.second.state == JobState::ACTIVE &&
            active_ids_at_start.count(kv.first) == 0) {
            newly_active.push_back({kv.second.submission_seq, kv.first});
        }
    }
    std::sort(newly_active.begin(), newly_active.end());
    for (const auto& entry : newly_active) {
        result.dispatched_job_ids.push_back(entry.second);
    }

    return result;
}

// ─── Cancel job ───────────────────────────────────────────────────────────────

void PortDispatcher::cancel_job(const std::string& job_id) {
    if (job_id.empty()) {
        throw std::invalid_argument("cancel_job: job_id must not be empty.");
    }
    JobRecord& rec = find_record(job_id);
    if (rec.state == JobState::COMPLETED || rec.state == JobState::CANCELLED) {
        throw std::invalid_argument(
            "cancel_job: job " + job_id + " is already COMPLETED or CANCELLED.");
    }

    // Remove from pending queue if present.
    remove_from_queue(job_id);

    // Release resources only if the job held any.
    release_job_resources(rec);
    rec.state = JobState::CANCELLED;

    // Attempt to dispatch the next waiting job.
    try_dispatch();
}

// ─── Queries ──────────────────────────────────────────────────────────────────

JobState PortDispatcher::job_state(const std::string& job_id) const {
    return find_record(job_id).state;
}

const JobRecord& PortDispatcher::job_record(const std::string& job_id) const {
    return find_record(job_id);
}

std::vector<std::string> PortDispatcher::ordered_queue() const {
    std::vector<std::string> ordered(pending_queue_.begin(), pending_queue_.end());
    std::stable_sort(ordered.begin(), ordered.end(),
        [this](const std::string& a, const std::string& b) {
            return queue_precedes(a, b);
        });
    return ordered;
}

int PortDispatcher::pending_count() const {
    return static_cast<int>(pending_queue_.size());
}

int PortDispatcher::active_count() const {
    int count = 0;
    for (const auto& kv : job_records_) {
        if (kv.second.state == JobState::ACTIVE) ++count;
    }
    return count;
}

bool PortDispatcher::crane_count_fulfilled(const std::string& job_id) const {
    const JobRecord& rec = find_record(job_id);
    return static_cast<int>(rec.active_cranes.size()) >= rec.job.cranes;
}
