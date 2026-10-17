#include "crane_registry.h"

// ─── Internal lookup helpers ──────────────────────────────────────────────────

CraneState& CraneRegistry::find_or_throw(const std::string& crane_id) {
    auto it = cranes_.find(crane_id);
    if (it == cranes_.end()) {
        throw std::invalid_argument("Crane not registered: " + crane_id);
    }
    return it->second;
}

const CraneState& CraneRegistry::find_or_throw(const std::string& crane_id) const {
    auto it = cranes_.find(crane_id);
    if (it == cranes_.end()) {
        throw std::invalid_argument("Crane not registered: " + crane_id);
    }
    return it->second;
}

// ─── Registration ─────────────────────────────────────────────────────────────

void CraneRegistry::register_crane(const std::string& crane_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }
    if (cranes_.find(crane_id) != cranes_.end()) {
        throw std::invalid_argument("Duplicate crane ID: " + crane_id);
    }
    cranes_[crane_id] = CraneState{crane_id, CraneStatus::AVAILABLE, ""};
}

// ─── Queries ──────────────────────────────────────────────────────────────────

bool CraneRegistry::has_crane(const std::string& crane_id) const {
    return cranes_.find(crane_id) != cranes_.end();
}

CraneState CraneRegistry::get_crane(const std::string& crane_id) const {
    return find_or_throw(crane_id);
}

std::vector<CraneState> CraneRegistry::all_cranes() const {
    std::vector<CraneState> result;
    result.reserve(cranes_.size());
    for (const auto& kv : cranes_) {
        result.push_back(kv.second);
    }
    return result;
}

std::vector<std::string> CraneRegistry::available_cranes() const {
    std::vector<std::string> result;
    result.reserve(cranes_.size());
    for (const auto& kv : cranes_) {
        if (kv.second.status == CraneStatus::AVAILABLE) {
            result.push_back(kv.first);
        }
    }
    return result;
}

int CraneRegistry::count_by_status(CraneStatus status) const {
    int count = 0;
    for (const auto& kv : cranes_) {
        if (kv.second.status == status) {
            ++count;
        }
    }
    return count;
}

int CraneRegistry::total_cranes() const {
    return static_cast<int>(cranes_.size());
}

// ─── Assignment ───────────────────────────────────────────────────────────────

void CraneRegistry::assign_crane(const std::string& crane_id,
                                  const std::string& job_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }
    if (job_id.empty()) {
        throw std::invalid_argument("Job ID must not be empty.");
    }

    CraneState& cs = find_or_throw(crane_id);

    if (cs.status == CraneStatus::ASSIGNED) {
        throw std::invalid_argument(
            "Cannot assign crane " + crane_id +
            ": already assigned to job " + cs.assigned_job);
    }
    if (cs.status == CraneStatus::FAILED) {
        throw std::invalid_argument(
            "Cannot assign crane " + crane_id +
            ": crane is FAILED; recover it first.");
    }

    // AVAILABLE -> ASSIGNED
    cs.status       = CraneStatus::ASSIGNED;
    cs.assigned_job = job_id;
}

// ─── Release ─────────────────────────────────────────────────────────────────

void CraneRegistry::release_crane(const std::string& crane_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }

    CraneState& cs = find_or_throw(crane_id);

    if (cs.status != CraneStatus::ASSIGNED) {
        if (cs.status == CraneStatus::AVAILABLE) {
            throw std::invalid_argument(
                "Cannot release crane " + crane_id +
                ": crane is already AVAILABLE (not assigned).");
        } else {
            throw std::invalid_argument(
                "Cannot release crane " + crane_id +
                ": crane is FAILED; recover it before releasing.");
        }
    }

    // ASSIGNED -> AVAILABLE
    cs.status       = CraneStatus::AVAILABLE;
    cs.assigned_job = "";
}

// ─── Failure ─────────────────────────────────────────────────────────────────

void CraneRegistry::mark_failed(const std::string& crane_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }

    CraneState& cs = find_or_throw(crane_id);

    if (cs.status == CraneStatus::FAILED) {
        throw std::invalid_argument(
            "Cannot fail crane " + crane_id + ": already FAILED.");
    }

    // AVAILABLE -> FAILED  or  ASSIGNED -> FAILED
    // Clear the assigned_job field if the crane was mid-job (the job itself is
    // now in an error state — callers must handle the job side separately).
    cs.status       = CraneStatus::FAILED;
    cs.assigned_job = "";
}

// ─── Recovery ────────────────────────────────────────────────────────────────

void CraneRegistry::recover_crane(const std::string& crane_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }

    CraneState& cs = find_or_throw(crane_id);

    if (cs.status != CraneStatus::FAILED) {
        if (cs.status == CraneStatus::AVAILABLE) {
            throw std::invalid_argument(
                "Cannot recover crane " + crane_id +
                ": crane is already AVAILABLE.");
        } else {
            throw std::invalid_argument(
                "Cannot recover crane " + crane_id +
                ": crane is ASSIGNED, not FAILED.");
        }
    }

    // FAILED -> AVAILABLE
    cs.status       = CraneStatus::AVAILABLE;
    cs.assigned_job = "";
}
