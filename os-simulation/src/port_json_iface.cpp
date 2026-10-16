// ─── port_json_iface.cpp ───────────────────────────────────────────────────────
//
// JSON-Lines command interface for the Container-Port-Flow simulation.
//
// Protocol (one JSON object per line, newline-terminated):
//
//   Request  → stdin   (one line per command)
//   Response → stdout  (one line per command)
//   Errors   → stderr  (diagnostics only, never mixed with stdout)
//
// Commands (field "cmd"):
//   "init"              – create PortDispatcher with capacities and policy
//   "register_crane"    – add a crane to the registry
//   "submit_job"        – enqueue a vessel job
//   "complete_job"      – mark an ACTIVE job done
//   "cancel_job"        – cancel PENDING / ACTIVE / INTERRUPTED job
//   "crane_failed"      – assigned crane failure
//   "idle_crane_failed" – idle crane failure
//   "crane_recovered"   – repair a FAILED crane
//   "dispatch_batch"    – trigger batch dispatch of queued jobs
//   "set_batch_mode"    – toggle batch mode on/off
//   "snapshot"          – dump current state
//   "shutdown"          – graceful exit; final response then process ends
//
// Every response has at least {"ok": true|false}.
// Error responses add {"error": "<message>"}.
//
// Compile (from os-simulation/):
//   g++ -std=c++14 -I include \
//       src/port_json_iface.cpp \
//       src/port_dispatcher.cpp \
//       src/disruption_manager.cpp \
//       src/crane_registry.cpp \
//       src/resource_manager.cpp \
//       -o port_json_iface.exe
//
// Usage:
//   port_json_iface.exe
//   (send JSON Lines on stdin; read JSON Lines from stdout)

#include "picojson.h"
#include "port_dispatcher.h"

#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

// ─── Helpers ──────────────────────────────────────────────────────────────────

// Safely get a string field from a picojson::object, or return default.
static std::string get_str(const picojson::object& obj,
                           const std::string& key,
                           const std::string& def = "") {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<std::string>()) return def;
    return it->second.get<std::string>();
}

// Safely get an integer field from a picojson::object, or return default.
// picojson stores all numbers as double; we cast.
static int get_int(const picojson::object& obj, const std::string& key, int def = 0) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<double>()) return def;
    return static_cast<int>(it->second.get<double>());
}

// Check a required string field is present and non-empty.
// Returns false and sets err on failure.
static bool require_str(const picojson::object& obj,
                        const std::string& key,
                        std::string& out,
                        std::string& err) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<std::string>()) {
        err = "missing or non-string field: " + key;
        return false;
    }
    out = it->second.get<std::string>();
    if (out.empty()) {
        err = "field must not be empty: " + key;
        return false;
    }
    return true;
}

// Check a required integer field is present.
static bool require_int(const picojson::object& obj,
                        const std::string& key,
                        int& out,
                        std::string& err) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<double>()) {
        err = "missing or non-numeric field: " + key;
        return false;
    }
    out = static_cast<int>(it->second.get<double>());
    return true;
}

// Emit a JSON-Lines response to stdout.
static void emit(const picojson::object& resp) {
    std::cout << picojson::value(resp).serialize() << "\n";
    std::cout.flush();
}

static void emit_ok() {
    picojson::object r;
    r["ok"] = picojson::value(true);
    emit(r);
}

static void emit_error(const std::string& msg) {
    picojson::object r;
    r["ok"]    = picojson::value(false);
    r["error"] = picojson::value(msg);
    emit(r);
}

// ─── JobState → string ────────────────────────────────────────────────────────

static std::string state_str(JobState s) {
    switch (s) {
        case JobState::PENDING:     return "PENDING";
        case JobState::ACTIVE:      return "ACTIVE";
        case JobState::INTERRUPTED: return "INTERRUPTED";
        case JobState::COMPLETED:   return "COMPLETED";
        case JobState::CANCELLED:   return "CANCELLED";
        default:                    return "UNKNOWN";
    }
}

// ─── DispatchPolicy parse ─────────────────────────────────────────────────────

static DispatchPolicy parse_policy(const std::string& s) {
    if (s == "SJF")      return DispatchPolicy::SJF;
    if (s == "PRIORITY") return DispatchPolicy::PRIORITY;
    return DispatchPolicy::FCFS;   // default
}

// ─── Adapter state ────────────────────────────────────────────────────────────

// We keep a unique_ptr so we can re-initialise on a second "init" command
// without restarting the process.
static std::unique_ptr<PortDispatcher> g_dp;

// Track every job_id ever submitted so we can build complete snapshots.
// PortDispatcher::job_record() throws for unknown IDs, so we guard with this set.
static std::unordered_set<std::string> g_known_jobs;

// ─── Command handlers ─────────────────────────────────────────────────────────

// "init": {"cmd":"init","berths":N,"cranes":N,"trucks":N,"policy":"FCFS"}
static void cmd_init(const picojson::object& req) {
    std::string err;
    int berths, cranes, trucks;
    if (!require_int(req, "berths", berths, err) ||
        !require_int(req, "cranes", cranes, err) ||
        !require_int(req, "trucks", trucks, err)) {
        emit_error(err);
        return;
    }
    std::string policy_str = get_str(req, "policy", "FCFS");
    DispatchPolicy policy  = parse_policy(policy_str);
    try {
        g_dp = std::unique_ptr<PortDispatcher>(
            new PortDispatcher(berths, cranes, trucks, policy));
        g_known_jobs.clear();

        bool batch_mode = false;
        auto it_batch = req.find("batch_mode");
        if (it_batch != req.end() && it_batch->second.is<bool>()) {
            batch_mode = it_batch->second.get<bool>();
        } else {
            auto it_auto = req.find("auto_dispatch");
            if (it_auto != req.end() && it_auto->second.is<bool>()) {
                batch_mode = !it_auto->second.get<bool>();
            }
        }
        if (batch_mode) {
            g_dp->set_auto_dispatch(false);
        }

        picojson::object r;
        r["ok"]         = picojson::value(true);
        r["policy"]     = picojson::value(policy_str);
        r["batch_mode"] = picojson::value(batch_mode);
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "register_crane": {"cmd":"register_crane","crane_id":"C1"}
static void cmd_register_crane(const picojson::object& req) {
    std::string err, crane_id;
    if (!require_str(req, "crane_id", crane_id, err)) { emit_error(err); return; }
    if (!g_dp) { emit_error("dispatcher not initialised; send init first"); return; }
    try {
        g_dp->register_crane(crane_id);
        picojson::object r;
        r["ok"]       = picojson::value(true);
        r["crane_id"] = picojson::value(crane_id);
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "submit_job": {
//   "cmd":"submit_job",
//   "job_id":"J1","arrival_time":0,"work_total":10,
//   "priority":1,"berths":1,"cranes":2,"trucks":0
// }
static void cmd_submit_job(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    std::string err, job_id;
    int arrival_time, work_total, priority, berths, cranes, trucks;
    if (!require_str(req, "job_id",       job_id,       err) ||
        !require_int(req, "arrival_time", arrival_time, err) ||
        !require_int(req, "work_total",   work_total,   err) ||
        !require_int(req, "priority",     priority,     err) ||
        !require_int(req, "berths",       berths,       err) ||
        !require_int(req, "cranes",       cranes,       err) ||
        !require_int(req, "trucks",       trucks,       err)) {
        emit_error(err);
        return;
    }
    PortJob pj;
    pj.job_id       = job_id;
    pj.arrival_time = arrival_time;
    pj.work_total   = work_total;
    pj.priority     = priority;
    pj.berths       = berths;
    pj.cranes       = cranes;
    pj.trucks       = trucks;
    try {
        g_dp->submit_job(pj);
        g_known_jobs.insert(job_id);
        picojson::object r;
        r["ok"]     = picojson::value(true);
        r["job_id"] = picojson::value(job_id);
        r["state"]  = picojson::value(state_str(g_dp->job_state(job_id)));
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "complete_job": {"cmd":"complete_job","job_id":"J1"}
static void cmd_complete_job(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    std::string err, job_id;
    if (!require_str(req, "job_id", job_id, err)) { emit_error(err); return; }
    try {
        g_dp->complete_job(job_id);
        picojson::object r;
        r["ok"]     = picojson::value(true);
        r["job_id"] = picojson::value(job_id);
        r["state"]  = picojson::value(std::string("COMPLETED"));
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "cancel_job": {"cmd":"cancel_job","job_id":"J1"}
static void cmd_cancel_job(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    std::string err, job_id;
    if (!require_str(req, "job_id", job_id, err)) { emit_error(err); return; }
    try {
        g_dp->cancel_job(job_id);
        picojson::object r;
        r["ok"]     = picojson::value(true);
        r["job_id"] = picojson::value(job_id);
        r["state"]  = picojson::value(std::string("CANCELLED"));
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "crane_failed": {
//   "cmd":"crane_failed","crane_id":"C1",
//   "work_completed":3,"work_remaining":7
// }
static void cmd_crane_failed(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    std::string err, crane_id;
    int work_completed, work_remaining;
    if (!require_str(req, "crane_id",       crane_id,       err) ||
        !require_int(req, "work_completed", work_completed, err) ||
        !require_int(req, "work_remaining", work_remaining, err)) {
        emit_error(err);
        return;
    }
    try {
        FailureResult fr = g_dp->on_crane_failed(crane_id, work_completed, work_remaining);
        picojson::object r;
        r["ok"]               = picojson::value(true);
        r["crane_id"]         = picojson::value(fr.crane_id);
        r["affected_job_id"]  = picojson::value(fr.affected_job_id);
        r["job_interrupted"]  = picojson::value(fr.job_interrupted);
        r["dispatched_next"]  = picojson::value(fr.dispatched_next);
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "idle_crane_failed": {"cmd":"idle_crane_failed","crane_id":"C2"}
static void cmd_idle_crane_failed(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    std::string err, crane_id;
    if (!require_str(req, "crane_id", crane_id, err)) { emit_error(err); return; }
    try {
        FailureResult fr = g_dp->on_idle_crane_failed(crane_id);
        picojson::object r;
        r["ok"]               = picojson::value(true);
        r["crane_id"]         = picojson::value(fr.crane_id);
        r["affected_job_id"]  = picojson::value(fr.affected_job_id);
        r["job_interrupted"]  = picojson::value(fr.job_interrupted);
        r["dispatched_next"]  = picojson::value(fr.dispatched_next);
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "crane_recovered": {"cmd":"crane_recovered","crane_id":"C1"}
static void cmd_crane_recovered(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    std::string err, crane_id;
    if (!require_str(req, "crane_id", crane_id, err)) { emit_error(err); return; }
    try {
        RecoveryResult rr = g_dp->on_crane_recovered(crane_id);
        picojson::object r;
        r["ok"]                  = picojson::value(true);
        r["crane_id"]            = picojson::value(rr.crane_id);
        r["job_dispatched"]      = picojson::value(rr.job_dispatched);
        r["dispatched_job_id"]   = picojson::value(rr.dispatched_job_id);
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "dispatch_batch": {"cmd":"dispatch_batch"}
static void cmd_dispatch_batch(const picojson::object& /*req*/) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    try {
        std::vector<std::string> dispatched = g_dp->flush_dispatch();
        picojson::object r;
        r["ok"] = picojson::value(true);
        picojson::array arr;
        for (const auto& jid : dispatched) {
            arr.push_back(picojson::value(jid));
        }
        r["dispatched_jobs"] = picojson::value(arr);
        r["count"]           = picojson::value(static_cast<double>(dispatched.size()));
        emit(r);
    } catch (const std::exception& ex) {
        emit_error(ex.what());
    }
}

// "set_batch_mode": {"cmd":"set_batch_mode","batch_mode":true}
static void cmd_set_batch_mode(const picojson::object& req) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }
    bool batch_mode = true;
    auto it_batch = req.find("batch_mode");
    if (it_batch != req.end() && it_batch->second.is<bool>()) {
        batch_mode = it_batch->second.get<bool>();
    } else {
        auto it_auto = req.find("auto_dispatch");
        if (it_auto != req.end() && it_auto->second.is<bool>()) {
            batch_mode = !it_auto->second.get<bool>();
        }
    }
    g_dp->set_auto_dispatch(!batch_mode);
    picojson::object r;
    r["ok"]         = picojson::value(true);
    r["batch_mode"] = picojson::value(batch_mode);
    emit(r);
}

// "snapshot": {"cmd":"snapshot"}
static void cmd_snapshot(const picojson::object& /*req*/) {
    if (!g_dp) { emit_error("dispatcher not initialised"); return; }

    picojson::object r;
    r["ok"] = picojson::value(true);
    r["batch_mode"] = picojson::value(!g_dp->auto_dispatch());
    std::string pol_s = "FCFS";
    if (g_dp->policy() == DispatchPolicy::SJF) pol_s = "SJF";
    else if (g_dp->policy() == DispatchPolicy::PRIORITY) pol_s = "PRIORITY";
    r["policy"] = picojson::value(pol_s);

    // Resource summary
    const ResourceManager& rm = g_dp->resource_manager();
    ResourceSet avail = rm.available();
    ResourceSet total = rm.total_capacity();
    picojson::object resources;
    resources["total_berths"]  = picojson::value(static_cast<double>(total.berths));
    resources["total_cranes"]  = picojson::value(static_cast<double>(total.cranes));
    resources["total_trucks"]  = picojson::value(static_cast<double>(total.trucks));
    resources["avail_berths"]  = picojson::value(static_cast<double>(avail.berths));
    resources["avail_cranes"]  = picojson::value(static_cast<double>(avail.cranes));
    resources["avail_trucks"]  = picojson::value(static_cast<double>(avail.trucks));
    r["resources"] = picojson::value(resources);

    // Queue counts
    r["pending_count"] = picojson::value(static_cast<double>(g_dp->pending_count()));
    r["active_count"]  = picojson::value(static_cast<double>(g_dp->active_count()));

    // Jobs
    picojson::array jobs_arr;
    for (const std::string& jid : g_known_jobs) {
        try {
            const JobRecord& rec = g_dp->job_record(jid);
            picojson::object jo;
            jo["job_id"]          = picojson::value(jid);
            jo["state"]           = picojson::value(state_str(rec.state));
            jo["work_total"]      = picojson::value(static_cast<double>(rec.job.work_total));
            jo["work_remaining"]  = picojson::value(static_cast<double>(rec.work_remaining));
            jo["work_completed"]  = picojson::value(static_cast<double>(rec.work_completed));
            jo["arrival_time"]    = picojson::value(static_cast<double>(rec.job.arrival_time));
            jo["priority"]        = picojson::value(static_cast<double>(rec.job.priority));
            jo["rm_allocated"]    = picojson::value(rec.rm_allocated);
            jo["required_berths"] = picojson::value(static_cast<double>(rec.job.berths));
            jo["required_cranes"] = picojson::value(static_cast<double>(rec.job.cranes));
            jo["required_trucks"] = picojson::value(static_cast<double>(rec.job.trucks));
            bool fulfilled        = g_dp->crane_count_fulfilled(jid);
            jo["crane_fulfilled"] = picojson::value(fulfilled);
            bool can_complete     = (rec.state == JobState::ACTIVE && fulfilled);
            jo["can_complete"]    = picojson::value(can_complete);
            // active crane IDs
            picojson::array cranes_arr;
            for (const std::string& cid : rec.active_cranes) {
                cranes_arr.push_back(picojson::value(cid));
            }
            jo["active_cranes"] = picojson::value(cranes_arr);
            jobs_arr.push_back(picojson::value(jo));
        } catch (...) {
            // Should not happen since g_known_jobs mirrors submitted IDs.
        }
    }
    r["jobs"] = picojson::value(jobs_arr);

    // Crane registry
    const CraneRegistry& reg = g_dp->crane_registry();
    picojson::array cranes_arr;
    for (const CraneState& cs : reg.all_cranes()) {
        picojson::object co;
        co["crane_id"] = picojson::value(cs.crane_id);
        std::string status_str;
        switch (cs.status) {
            case CraneStatus::AVAILABLE: status_str = "AVAILABLE"; break;
            case CraneStatus::ASSIGNED:  status_str = "ASSIGNED";  break;
            case CraneStatus::FAILED:    status_str = "FAILED";    break;
        }
        co["status"]       = picojson::value(status_str);
        co["assigned_job"] = picojson::value(cs.assigned_job);
        cranes_arr.push_back(picojson::value(co));
    }
    r["cranes"] = picojson::value(cranes_arr);

    emit(r);
}

// "shutdown": {"cmd":"shutdown"}
static void cmd_shutdown() {
    picojson::object r;
    r["ok"]  = picojson::value(true);
    r["msg"] = picojson::value(std::string("shutdown"));
    emit(r);
}

// ─── Main event loop ──────────────────────────────────────────────────────────

int main() {
    // Disable sync so std::cin / std::cout are fast; we own both streams.
    std::ios::sync_with_stdio(false);

    std::string line;
    while (std::getline(std::cin, line)) {
        // Strip trailing CR (Windows line endings)
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;

        picojson::value req_val;
        std::string parse_err = picojson::parse(req_val, line);
        if (!parse_err.empty()) {
            emit_error("JSON parse error: " + parse_err);
            continue;
        }
        if (!req_val.is<picojson::object>()) {
            emit_error("request must be a JSON object");
            continue;
        }

        const picojson::object& req = req_val.get<picojson::object>();
        std::string cmd = get_str(req, "cmd");
        if (cmd.empty()) {
            emit_error("missing or empty field: cmd");
            continue;
        }

        if      (cmd == "init")              cmd_init(req);
        else if (cmd == "register_crane")    cmd_register_crane(req);
        else if (cmd == "submit_job")        cmd_submit_job(req);
        else if (cmd == "complete_job")      cmd_complete_job(req);
        else if (cmd == "cancel_job")        cmd_cancel_job(req);
        else if (cmd == "crane_failed")      cmd_crane_failed(req);
        else if (cmd == "idle_crane_failed") cmd_idle_crane_failed(req);
        else if (cmd == "crane_recovered")   cmd_crane_recovered(req);
        else if (cmd == "dispatch_batch" || cmd == "dispatch") cmd_dispatch_batch(req);
        else if (cmd == "set_batch_mode")    cmd_set_batch_mode(req);
        else if (cmd == "snapshot")          cmd_snapshot(req);
        else if (cmd == "shutdown") {
            cmd_shutdown();
            break;
        }
        else {
            emit_error("unknown command: " + cmd);
        }
    }

    return 0;
}
