#ifndef CRANE_REGISTRY_H
#define CRANE_REGISTRY_H

#include <string>
#include <unordered_map>
#include <vector>
#include <stdexcept>

// ─── Crane status ─────────────────────────────────────────────────────────────
// A crane is always in exactly one of three states.
//
// Valid transitions:
//   AVAILABLE  ->  ASSIGNED   (assign_crane)
//   AVAILABLE  ->  FAILED     (mark_failed)
//   ASSIGNED   ->  AVAILABLE  (release_crane, after job completes normally)
//   ASSIGNED   ->  FAILED     (mark_failed, crane breaks mid-job)
//   FAILED     ->  AVAILABLE  (recover_crane, maintenance complete)
//
// Disallowed transitions (throw std::invalid_argument):
//   FAILED    ->  ASSIGNED   (must recover before assigning)
//   AVAILABLE ->  AVAILABLE  (not assigned; release_crane requires ASSIGNED)
//   ASSIGNED  ->  ASSIGNED   (already assigned to a job)
//   FAILED    ->  FAILED     (already failed)

enum class CraneStatus {
    AVAILABLE,  // idle, ready to be assigned
    ASSIGNED,   // currently serving a job/ship
    FAILED      // out of service; must be recovered before reuse
};

// ─── Per-crane state ──────────────────────────────────────────────────────────

struct CraneState {
    std::string crane_id;       // unique, non-empty identifier
    CraneStatus status;         // current status
    std::string assigned_job;   // non-empty only when status == ASSIGNED
};

// ─── Crane registry ───────────────────────────────────────────────────────────
// Tracks individual cranes by ID and enforces valid state transitions.
//
// Invariants maintained at all times:
//   * crane_id is unique and non-empty for every registered crane.
//   * assigned_job is non-empty iff status == ASSIGNED.
//   * A FAILED crane is never returned by available_cranes() or counted
//     as available in count_by_status(CraneStatus::AVAILABLE).
//   * State changes are rejected with std::invalid_argument on any violation.
//
// ResourceManager integration note:
//   CraneRegistry tracks individual crane identity and status.  The existing
//   ResourceManager tracks aggregate crane *quantities* (how many cranes total
//   a job holds).  The two components maintain separate, complementary views.
//
//   Safe synchronisation boundary (caller-mediated coupling):
//     - When allocating: call ResourceManager::allocate() for quantities, then
//       call CraneRegistry::assign_crane() for each specific crane ID.
//     - When releasing: call ResourceManager::release() for quantities, then
//       call CraneRegistry::release_crane() for each specific crane ID.
//     - Neither component calls the other.  The caller keeps them consistent.
//     - This prevents double releases and incorrect allocation counts without
//       introducing a circular or tight dependency between the two modules.
//
//   LIMITATION (Milestone 1):
//     Full automated synchronisation is NOT implemented.  The aggregate
//     available_cranes count in ResourceManager and the per-crane AVAILABLE
//     count in CraneRegistry can diverge if a caller fails cranes without
//     updating ResourceManager, or releases via ResourceManager without calling
//     release_crane().  Callers must maintain consistency explicitly.

class CraneRegistry {
public:
    // ── Registration ─────────────────────────────────────────────────────────

    // Register a new crane.  Initial status is AVAILABLE.
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * duplicate crane_id (already registered)
    void register_crane(const std::string& crane_id);

    // ── Queries ──────────────────────────────────────────────────────────────

    // Returns true if a crane with crane_id has been registered.
    bool has_crane(const std::string& crane_id) const;

    // Returns the current state of the named crane.
    // Throws std::invalid_argument if the crane is not registered.
    CraneState get_crane(const std::string& crane_id) const;

    // Returns a snapshot of all registered crane states (order unspecified).
    std::vector<CraneState> all_cranes() const;

    // Returns IDs of all cranes currently in AVAILABLE status.
    std::vector<std::string> available_cranes() const;

    // Returns the number of cranes whose status equals the given status.
    int count_by_status(CraneStatus status) const;

    // Total number of registered cranes.
    int total_cranes() const;

    // ── Assignment ───────────────────────────────────────────────────────────

    // Assign an AVAILABLE crane to a job.
    // Throws std::invalid_argument on:
    //   * empty crane_id or empty job_id
    //   * crane not registered
    //   * crane is not AVAILABLE (ASSIGNED or FAILED)
    void assign_crane(const std::string& crane_id, const std::string& job_id);

    // ── Release ──────────────────────────────────────────────────────────────

    // Release an ASSIGNED crane back to AVAILABLE (job completed normally).
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * crane not registered
    //   * crane is not ASSIGNED (e.g., already AVAILABLE or FAILED)
    void release_crane(const std::string& crane_id);

    // ── Failure & recovery ───────────────────────────────────────────────────

    // Mark a crane as FAILED.  Valid from AVAILABLE or ASSIGNED.
    // A FAILED crane cannot be assigned to a new job until recovered.
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * crane not registered
    //   * crane is already FAILED
    void mark_failed(const std::string& crane_id);

    // Recover a FAILED crane to AVAILABLE.
    // Throws std::invalid_argument on:
    //   * empty crane_id
    //   * crane not registered
    //   * crane is not FAILED
    void recover_crane(const std::string& crane_id);

private:
    // Keyed by crane_id.
    std::unordered_map<std::string, CraneState> cranes_;

    // Internal lookup helpers.
    CraneState& find_or_throw(const std::string& crane_id);
    const CraneState& find_or_throw(const std::string& crane_id) const;
};

#endif // CRANE_REGISTRY_H
