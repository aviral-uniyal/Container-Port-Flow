#include "disruption_manager.h"

// ─── Construction ─────────────────────────────────────────────────────────────

DisruptionManager::DisruptionManager(CraneRegistry& registry)
    : registry_(registry) {}

// ─── Idle-crane failure ───────────────────────────────────────────────────────

DisruptionResult DisruptionManager::fail_idle_crane(const std::string& crane_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }

    // Verify the crane is registered before checking status.
    CraneState cs = registry_.get_crane(crane_id);  // throws if unregistered

    if (cs.status != CraneStatus::AVAILABLE) {
        if (cs.status == CraneStatus::ASSIGNED) {
            throw std::invalid_argument(
                "fail_idle_crane: crane " + crane_id +
                " is ASSIGNED (use fail_assigned_crane instead).");
        } else {
            throw std::invalid_argument(
                "fail_idle_crane: crane " + crane_id +
                " is already FAILED.");
        }
    }

    // Delegate to registry (AVAILABLE -> FAILED).
    registry_.mark_failed(crane_id);

    DisruptionResult result;
    result.event    = DisruptionEvent::CRANE_FAILED;
    result.crane_id = crane_id;
    // affected_job stays default-constructed: job_id == ""
    return result;
}

// ─── Assigned-crane failure ───────────────────────────────────────────────────

DisruptionResult DisruptionManager::fail_assigned_crane(
    const std::string& crane_id,
    int work_completed,
    int work_remaining)
{
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }
    if (work_completed < 0) {
        throw std::invalid_argument(
            "work_completed must be >= 0 for crane " + crane_id);
    }
    if (work_remaining <= 0) {
        throw std::invalid_argument(
            "work_remaining must be > 0 for crane " + crane_id);
    }

    // Read the crane state BEFORE modifying anything.
    // This also throws if the crane is unregistered.
    CraneState cs = registry_.get_crane(crane_id);

    if (cs.status != CraneStatus::ASSIGNED) {
        if (cs.status == CraneStatus::AVAILABLE) {
            throw std::invalid_argument(
                "fail_assigned_crane: crane " + crane_id +
                " is AVAILABLE (use fail_idle_crane instead).");
        } else {
            throw std::invalid_argument(
                "fail_assigned_crane: crane " + crane_id +
                " is already FAILED.");
        }
    }

    // Capture job progress NOW, before mark_failed() clears assigned_job.
    JobProgress progress;
    progress.job_id         = cs.assigned_job;   // non-empty: crane was ASSIGNED
    progress.crane_id       = crane_id;
    progress.work_completed = work_completed;
    progress.work_remaining = work_remaining;

    // Persist the record (overwrite if crane was previously failed & cleared).
    job_progress_[crane_id] = progress;

    // Delegate to registry (ASSIGNED -> FAILED).
    // After this call cs.assigned_job is "" inside the registry, but we have
    // the value safely captured above.
    registry_.mark_failed(crane_id);

    DisruptionResult result;
    result.event        = DisruptionEvent::CRANE_FAILED;
    result.crane_id     = crane_id;
    result.affected_job = progress;
    return result;
}

// ─── Crane recovery ───────────────────────────────────────────────────────────

DisruptionResult DisruptionManager::recover_crane(const std::string& crane_id) {
    if (crane_id.empty()) {
        throw std::invalid_argument("Crane ID must not be empty.");
    }

    // Verify registered and currently FAILED (registry throws otherwise).
    // We do the state check explicitly for a better error message.
    CraneState cs = registry_.get_crane(crane_id);  // throws if unregistered

    if (cs.status != CraneStatus::FAILED) {
        if (cs.status == CraneStatus::AVAILABLE) {
            throw std::invalid_argument(
                "recover_crane: crane " + crane_id +
                " is already AVAILABLE (not FAILED).");
        } else {
            throw std::invalid_argument(
                "recover_crane: crane " + crane_id +
                " is ASSIGNED (not FAILED).");
        }
    }

    // Delegate to registry (FAILED -> AVAILABLE).
    registry_.recover_crane(crane_id);

    DisruptionResult result;
    result.event    = DisruptionEvent::CRANE_RECOVERED;
    result.crane_id = crane_id;
    // affected_job stays default-constructed
    return result;
}

// ─── Job-progress queries ─────────────────────────────────────────────────────

bool DisruptionManager::has_job_progress(const std::string& crane_id) const {
    return job_progress_.count(crane_id) > 0;
}

JobProgress DisruptionManager::get_job_progress(const std::string& crane_id) const {
    auto it = job_progress_.find(crane_id);
    if (it == job_progress_.end()) {
        throw std::invalid_argument(
            "No job-progress record for crane: " + crane_id);
    }
    return it->second;
}

std::vector<JobProgress> DisruptionManager::affected_jobs() const {
    std::vector<JobProgress> result;
    result.reserve(job_progress_.size());
    for (const auto& kv : job_progress_) {
        result.push_back(kv.second);
    }
    return result;
}

void DisruptionManager::clear_job_progress(const std::string& crane_id) {
    auto it = job_progress_.find(crane_id);
    if (it == job_progress_.end()) {
        throw std::invalid_argument(
            "No job-progress record to clear for crane: " + crane_id);
    }
    job_progress_.erase(it);
}

void DisruptionManager::clear_all_job_progress() {
    job_progress_.clear();
}
