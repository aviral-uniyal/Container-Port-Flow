#ifndef RESOURCE_MANAGER_H
#define RESOURCE_MANAGER_H

#include <string>
#include <unordered_map>
#include <stdexcept>

// ─── Resource types ──────────────────────────────────────────────────────────
// Typed quantities for the three port resource categories.

struct ResourceSet {
    int berths;
    int cranes;
    int trucks;
};

// ─── Resource request ────────────────────────────────────────────────────────
// A vessel/job specifies its ID and required quantities of each resource.

struct ResourceRequest {
    std::string job_id;       // unique, non-empty identifier
    int berths;               // >= 0
    int cranes;               // >= 0
    int trucks;               // >= 0
};

// ─── Resource manager ────────────────────────────────────────────────────────
// Manages a fixed pool of port resources (berths, cranes, trucks).
//
// Invariants maintained at all times:
//   • available = total - sum(all active allocations)
//   • 0 <= available[r] <= total[r]  for each resource r
//   • No partial allocations: a job gets all requested resources or none.
//   • Each active job ID is unique; duplicate allocations are rejected.
//   • Failed requests leave all resource counts unchanged.

class ResourceManager {
public:
    // Construct with total capacities.  All values must be >= 0.
    // Throws std::invalid_argument if any capacity is negative.
    ResourceManager(int total_berths, int total_cranes, int total_trucks);

    // ── Queries ──────────────────────────────────────────────────────────

    // Total (fixed) capacity for each resource.
    ResourceSet total_capacity() const;

    // Currently available (unallocated) quantity of each resource.
    ResourceSet available() const;

    // Currently allocated (in-use) quantity of each resource.
    ResourceSet allocated() const;

    // Number of active jobs holding resources.
    int active_job_count() const;

    // Returns true if `job_id` currently holds an active allocation.
    bool has_active_job(const std::string& job_id) const;

    // Returns the allocation held by `job_id`.
    // Throws std::invalid_argument if the job is not active.
    ResourceSet get_job_allocation(const std::string& job_id) const;

    // ── Can-satisfy check ────────────────────────────────────────────────

    // Returns true if the request could be satisfied right now.
    // Does NOT modify state.  Validates the request first.
    // Throws std::invalid_argument on invalid request (empty ID, negative qty).
    bool can_allocate(const ResourceRequest& req) const;

    // ── Allocation ───────────────────────────────────────────────────────

    // Atomically allocates the requested resources.
    // Returns true on success, false if insufficient resources.
    // Throws std::invalid_argument on:
    //   • empty job_id
    //   • negative resource quantity
    //   • duplicate active job_id
    //   • request exceeding total capacity (can never be satisfied)
    // On failure (returns false), resource state is unchanged.
    bool allocate(const ResourceRequest& req);

    // ── Release ──────────────────────────────────────────────────────────

    // Releases all resources held by `job_id`.
    // Throws std::invalid_argument on:
    //   • empty job_id
    //   • unknown / inactive job_id
    void release(const std::string& job_id);

private:
    int total_berths_;
    int total_cranes_;
    int total_trucks_;

    int available_berths_;
    int available_cranes_;
    int available_trucks_;

    // Active allocations keyed by job_id.
    std::unordered_map<std::string, ResourceSet> active_allocations_;

    // Validates a request and throws on invalid fields.
    void validate_request(const ResourceRequest& req) const;
};

#endif // RESOURCE_MANAGER_H
