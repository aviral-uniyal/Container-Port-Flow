#include "resource_manager.h"

// ─── Construction ────────────────────────────────────────────────────────────

ResourceManager::ResourceManager(int total_berths,
                                 int total_cranes,
                                 int total_trucks)
    : total_berths_(total_berths),
      total_cranes_(total_cranes),
      total_trucks_(total_trucks),
      available_berths_(total_berths),
      available_cranes_(total_cranes),
      available_trucks_(total_trucks) {

    if (total_berths < 0 || total_cranes < 0 || total_trucks < 0) {
        throw std::invalid_argument(
            "Total resource capacities must be non-negative.");
    }
}

// ─── Queries ─────────────────────────────────────────────────────────────────

ResourceSet ResourceManager::total_capacity() const {
    return {total_berths_, total_cranes_, total_trucks_};
}

ResourceSet ResourceManager::available() const {
    return {available_berths_, available_cranes_, available_trucks_};
}

ResourceSet ResourceManager::allocated() const {
    return {total_berths_  - available_berths_,
            total_cranes_  - available_cranes_,
            total_trucks_  - available_trucks_};
}

int ResourceManager::active_job_count() const {
    return static_cast<int>(active_allocations_.size());
}

bool ResourceManager::has_active_job(const std::string& job_id) const {
    return active_allocations_.find(job_id) != active_allocations_.end();
}

ResourceSet ResourceManager::get_job_allocation(const std::string& job_id) const {
    auto it = active_allocations_.find(job_id);
    if (it == active_allocations_.end()) {
        throw std::invalid_argument(
            "Job is not active: " + job_id);
    }
    return it->second;
}

// ─── Validation ──────────────────────────────────────────────────────────────

void ResourceManager::validate_request(const ResourceRequest& req) const {
    if (req.job_id.empty()) {
        throw std::invalid_argument("Job ID must not be empty.");
    }
    if (req.berths < 0 || req.cranes < 0 || req.trucks < 0) {
        throw std::invalid_argument(
            "Resource quantities must be non-negative for job " + req.job_id);
    }
    if (req.berths > total_berths_ ||
        req.cranes > total_cranes_ ||
        req.trucks > total_trucks_) {
        throw std::invalid_argument(
            "Request exceeds total capacity for job " + req.job_id);
    }
}

// ─── Can-satisfy check ──────────────────────────────────────────────────────

bool ResourceManager::can_allocate(const ResourceRequest& req) const {
    validate_request(req);

    return req.berths <= available_berths_ &&
           req.cranes <= available_cranes_ &&
           req.trucks <= available_trucks_;
}

// ─── Allocation ──────────────────────────────────────────────────────────────

bool ResourceManager::allocate(const ResourceRequest& req) {
    validate_request(req);

    if (active_allocations_.find(req.job_id) != active_allocations_.end()) {
        throw std::invalid_argument(
            "Duplicate active allocation for job " + req.job_id);
    }

    // Check availability — return false (no exception) if insufficient.
    if (req.berths > available_berths_ ||
        req.cranes > available_cranes_ ||
        req.trucks > available_trucks_) {
        return false;
    }

    // Commit atomically.
    available_berths_ -= req.berths;
    available_cranes_ -= req.cranes;
    available_trucks_ -= req.trucks;

    active_allocations_[req.job_id] = {req.berths, req.cranes, req.trucks};
    return true;
}

// ─── Release ─────────────────────────────────────────────────────────────────

void ResourceManager::release(const std::string& job_id) {
    if (job_id.empty()) {
        throw std::invalid_argument("Job ID must not be empty.");
    }

    auto it = active_allocations_.find(job_id);
    if (it == active_allocations_.end()) {
        throw std::invalid_argument(
            "Cannot release: job is not active: " + job_id);
    }

    const ResourceSet& held = it->second;
    available_berths_ += held.berths;
    available_cranes_ += held.cranes;
    available_trucks_ += held.trucks;

    active_allocations_.erase(it);
}
