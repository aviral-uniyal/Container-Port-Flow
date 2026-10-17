// ─── port_json_iface_tests.cpp ────────────────────────────────────────────────
//
// Test suite for the JSON-Lines command interface.
// Tests: JSON parsing, command validation, multi-command session,
//        state persistence, job lifecycle, crane disruptions, snapshots.
//
// Compile (from os-simulation/):
//   g++ -std=c++14 -I include \
//       tests/port_json_iface_tests.cpp \
//       src/port_dispatcher.cpp \
//       src/disruption_manager.cpp \
//       src/crane_registry.cpp \
//       src/resource_manager.cpp \
//       -o port_json_iface_tests.exe
// Run:
//   ./port_json_iface_tests.exe

#include "picojson.h"
#include "port_dispatcher.h"

#include <cassert>
#include <algorithm>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_set>
#include <unordered_map>
#include <vector>

// ─── Minimal test infrastructure ─────────────────────────────────────────────

static int g_total  = 0;
static int g_passed = 0;
static int g_failed = 0;

static void run_test(const std::string& name, bool (*fn)()) {
    ++g_total;
    std::cerr << "[ RUN  ] " << name << "\n";
    bool ok = false;
    try {
        ok = fn();
    } catch (const std::exception& ex) {
        std::cerr << "  EXCEPTION: " << ex.what() << "\n";
    } catch (...) {
        std::cerr << "  UNKNOWN EXCEPTION\n";
    }
    if (ok) {
        ++g_passed;
        std::cerr << "[ PASS ] " << name << "\n";
    } else {
        ++g_failed;
        std::cerr << "[ FAIL ] " << name << "\n";
    }
}

#define CHECK(cond) \
    do { if (!(cond)) { \
        std::cerr << "  CHECK FAILED: " #cond " at line " << __LINE__ << "\n"; \
        return false; \
    }} while (0)

#define CHECK_EQ(a, b) \
    do { if ((a) != (b)) { \
        std::cerr << "  CHECK_EQ FAILED: " #a " [" << (a) << "] != " #b " [" << (b) << "] at line " << __LINE__ << "\n"; \
        return false; \
    }} while (0)

#define CHECK_THROWS(expr) \
    do { bool threw = false; try { (void)(expr); } catch (...) { threw = true; } \
        if (!threw) { \
            std::cerr << "  CHECK_THROWS FAILED: " #expr " at line " << __LINE__ << "\n"; \
            return false; \
        } \
    } while (0)

// ─── picojson helper wrappers (mirrors helpers in the adapter) ────────────────

static std::string get_str(const picojson::object& obj,
                            const std::string& key,
                            const std::string& def = "") {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<std::string>()) return def;
    return it->second.get<std::string>();
}
static bool get_bool(const picojson::object& obj, const std::string& key, bool def = false) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<bool>()) return def;
    return it->second.get<bool>();
}
static int get_int(const picojson::object& obj, const std::string& key, int def = 0) {
    auto it = obj.find(key);
    if (it == obj.end() || !it->second.is<double>()) return def;
    return static_cast<int>(it->second.get<double>());
}

// ─── Simulate adapter logic inline for testing ───────────────────────────────
// The adapter process is not spawned; instead we instantiate PortDispatcher
// directly and replicate the per-command logic.  This lets us test command
// validation, state persistence, and output shape without subprocess I/O.

struct AdapterSim {
    std::unique_ptr<PortDispatcher> dp;
    std::unordered_set<std::string> known_jobs;
    bool initialised = false;

    // Returns a parsed response object.
    picojson::object dispatch(const std::string& json_line) {
        picojson::value v;
        std::string err = picojson::parse(v, json_line);
        if (!err.empty()) {
            picojson::object r;
            r["ok"]    = picojson::value(false);
            r["error"] = picojson::value("JSON parse error: " + err);
            return r;
        }
        if (!v.is<picojson::object>()) {
            picojson::object r;
            r["ok"]    = picojson::value(false);
            r["error"] = picojson::value("request must be a JSON object");
            return r;
        }
        const picojson::object& req = v.get<picojson::object>();
        std::string cmd = get_str(req, "cmd");
        if (cmd == "init")              return do_init(req);
        if (cmd == "register_crane")    return do_register_crane(req);
        if (cmd == "submit_job")        return do_submit_job(req);
        if (cmd == "complete_job")      return do_complete_job(req);
        if (cmd == "cancel_job")        return do_cancel_job(req);
        if (cmd == "crane_failed")      return do_crane_failed(req);
        if (cmd == "idle_crane_failed") return do_idle_crane_failed(req);
        if (cmd == "crane_recovered")   return do_crane_recovered(req);
        if (cmd == "dispatch_batch" || cmd == "dispatch") return do_dispatch_batch(req);
        if (cmd == "set_batch_mode")    return do_set_batch_mode(req);
        if (cmd == "snapshot")          return do_snapshot();
        if (cmd == "shutdown") {
            picojson::object r;
            r["ok"]  = picojson::value(true);
            r["msg"] = picojson::value(std::string("shutdown"));
            return r;
        }
        picojson::object r;
        r["ok"]    = picojson::value(false);
        r["error"] = picojson::value("unknown command: " + cmd);
        return r;
    }

private:
    picojson::object err_resp(const std::string& msg) {
        picojson::object r;
        r["ok"]    = picojson::value(false);
        r["error"] = picojson::value(msg);
        return r;
    }
    picojson::object ok_resp() {
        picojson::object r; r["ok"] = picojson::value(true); return r;
    }

    std::string state_str(JobState s) {
        switch (s) {
            case JobState::PENDING:     return "PENDING";
            case JobState::ACTIVE:      return "ACTIVE";
            case JobState::INTERRUPTED: return "INTERRUPTED";
            case JobState::COMPLETED:   return "COMPLETED";
            case JobState::CANCELLED:   return "CANCELLED";
            default:                    return "UNKNOWN";
        }
    }

    picojson::object do_init(const picojson::object& req) {
        auto bi = req.find("berths"); auto ci = req.find("cranes"); auto ti = req.find("trucks");
        if (bi == req.end() || !bi->second.is<double>()) return err_resp("missing or non-numeric field: berths");
        if (ci == req.end() || !ci->second.is<double>()) return err_resp("missing or non-numeric field: cranes");
        if (ti == req.end() || !ti->second.is<double>()) return err_resp("missing or non-numeric field: trucks");
        int berths = (int)bi->second.get<double>();
        int cranes = (int)ci->second.get<double>();
        int trucks = (int)ti->second.get<double>();
        std::string ps = get_str(req, "policy", "FCFS");
        DispatchPolicy pol = DispatchPolicy::FCFS;
        if (ps == "SJF")      pol = DispatchPolicy::SJF;
        if (ps == "PRIORITY") pol = DispatchPolicy::PRIORITY;
        try {
            dp = std::unique_ptr<PortDispatcher>(new PortDispatcher(berths, cranes, trucks, pol));
            known_jobs.clear(); initialised = true;
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
                dp->set_auto_dispatch(false);
            }
            picojson::object r; r["ok"] = picojson::value(true); r["policy"] = picojson::value(ps);
            r["batch_mode"] = picojson::value(batch_mode);
            return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_register_crane(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised; send init first");
        auto it = req.find("crane_id");
        if (it == req.end() || !it->second.is<std::string>()) return err_resp("missing or non-string field: crane_id");
        std::string cid = it->second.get<std::string>();
        if (cid.empty()) return err_resp("field must not be empty: crane_id");
        try {
            dp->register_crane(cid);
            picojson::object r; r["ok"] = picojson::value(true); r["crane_id"] = picojson::value(cid); return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_submit_job(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
        auto jit = req.find("job_id");
        if (jit == req.end() || !jit->second.is<std::string>() || jit->second.get<std::string>().empty())
            return err_resp("missing or empty field: job_id");
        std::string jid = jit->second.get<std::string>();
        auto getI = [&](const std::string& k, int& v, std::string& e) {
            auto it = req.find(k);
            if (it == req.end() || !it->second.is<double>()) { e = "missing or non-numeric field: " + k; return false; }
            v = (int)it->second.get<double>(); return true;
        };
        std::string e; int at,wt,pri,b,c,t;
        if (!getI("arrival_time",at,e)||!getI("work_total",wt,e)||!getI("priority",pri,e)||
            !getI("berths",b,e)||!getI("cranes",c,e)||!getI("trucks",t,e)) return err_resp(e);
        PortJob pj; pj.job_id=jid; pj.arrival_time=at; pj.work_total=wt;
        pj.priority=pri; pj.berths=b; pj.cranes=c; pj.trucks=t;
        try {
            dp->submit_job(pj); known_jobs.insert(jid);
            picojson::object r; r["ok"]=picojson::value(true);
            r["job_id"]=picojson::value(jid);
            r["state"]=picojson::value(state_str(dp->job_state(jid)));
            return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_complete_job(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
        auto it = req.find("job_id");
        if (it == req.end() || !it->second.is<std::string>() || it->second.get<std::string>().empty())
            return err_resp("missing or empty field: job_id");
        std::string jid = it->second.get<std::string>();
        try { dp->complete_job(jid);
            picojson::object r; r["ok"]=picojson::value(true); r["job_id"]=picojson::value(jid);
            r["state"]=picojson::value(std::string("COMPLETED")); return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_cancel_job(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
        auto it = req.find("job_id");
        if (it == req.end() || !it->second.is<std::string>() || it->second.get<std::string>().empty())
            return err_resp("missing or empty field: job_id");
        std::string jid = it->second.get<std::string>();
        try { dp->cancel_job(jid);
            picojson::object r; r["ok"]=picojson::value(true); r["job_id"]=picojson::value(jid);
            r["state"]=picojson::value(std::string("CANCELLED")); return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_crane_failed(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
        auto ci = req.find("crane_id"); auto wci = req.find("work_completed"); auto wri = req.find("work_remaining");
        if (ci==req.end()||!ci->second.is<std::string>()||ci->second.get<std::string>().empty())
            return err_resp("missing or empty field: crane_id");
        if (wci==req.end()||!wci->second.is<double>()) return err_resp("missing or non-numeric field: work_completed");
        if (wri==req.end()||!wri->second.is<double>()) return err_resp("missing or non-numeric field: work_remaining");
        std::string cid = ci->second.get<std::string>();
        int wc=(int)wci->second.get<double>(), wr=(int)wri->second.get<double>();
        try {
            FailureResult fr = dp->on_crane_failed(cid, wc, wr);
            picojson::object r; r["ok"]=picojson::value(true);
            r["crane_id"]=picojson::value(fr.crane_id);
            r["affected_job_id"]=picojson::value(fr.affected_job_id);
            r["job_interrupted"]=picojson::value(fr.job_interrupted);
            r["dispatched_next"]=picojson::value(fr.dispatched_next);
            return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_idle_crane_failed(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
        auto ci = req.find("crane_id");
        if (ci==req.end()||!ci->second.is<std::string>()||ci->second.get<std::string>().empty())
            return err_resp("missing or empty field: crane_id");
        std::string cid = ci->second.get<std::string>();
        try {
            FailureResult fr = dp->on_idle_crane_failed(cid);
            picojson::object r; r["ok"]=picojson::value(true);
            r["crane_id"]=picojson::value(fr.crane_id);
            r["affected_job_id"]=picojson::value(fr.affected_job_id);
            r["job_interrupted"]=picojson::value(fr.job_interrupted);
            r["dispatched_next"]=picojson::value(fr.dispatched_next);
            return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_crane_recovered(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
        auto ci = req.find("crane_id");
        if (ci==req.end()||!ci->second.is<std::string>()||ci->second.get<std::string>().empty())
            return err_resp("missing or empty field: crane_id");
        std::string cid = ci->second.get<std::string>();
        try {
            RecoveryResult rr = dp->on_crane_recovered(cid);
            picojson::object r; r["ok"]=picojson::value(true);
            r["crane_id"]=picojson::value(rr.crane_id);
            r["job_dispatched"]=picojson::value(rr.job_dispatched);
            r["dispatched_job_id"]=picojson::value(rr.dispatched_job_id);
            return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_dispatch_batch(const picojson::object& /*req*/) {
        if (!initialised) return err_resp("dispatcher not initialised");
        try {
            std::vector<std::string> dispatched = dp->flush_dispatch();
            picojson::object r;
            r["ok"] = picojson::value(true);
            picojson::array arr;
            for (const auto& jid : dispatched) arr.push_back(picojson::value(jid));
            r["dispatched_jobs"] = picojson::value(arr);
            r["count"] = picojson::value(static_cast<double>(dispatched.size()));
            return r;
        } catch (const std::exception& ex) { return err_resp(ex.what()); }
    }

    picojson::object do_set_batch_mode(const picojson::object& req) {
        if (!initialised) return err_resp("dispatcher not initialised");
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
        dp->set_auto_dispatch(!batch_mode);
        picojson::object r;
        r["ok"] = picojson::value(true);
        r["batch_mode"] = picojson::value(batch_mode);
        return r;
    }

    picojson::object do_snapshot() {
        if (!initialised) return err_resp("dispatcher not initialised");
        picojson::object r; r["ok"] = picojson::value(true);
        r["batch_mode"] = picojson::value(!dp->auto_dispatch());
        std::string pol_s = "FCFS";
        if (dp->policy() == DispatchPolicy::SJF) pol_s = "SJF";
        else if (dp->policy() == DispatchPolicy::PRIORITY) pol_s = "PRIORITY";
        r["policy"] = picojson::value(pol_s);
        const ResourceManager& rm = dp->resource_manager();
        ResourceSet avail = rm.available(), total = rm.total_capacity();
        picojson::object res;
        res["total_berths"] = picojson::value((double)total.berths);
        res["total_cranes"] = picojson::value((double)total.cranes);
        res["total_trucks"] = picojson::value((double)total.trucks);
        res["avail_berths"] = picojson::value((double)avail.berths);
        res["avail_cranes"] = picojson::value((double)avail.cranes);
        res["avail_trucks"] = picojson::value((double)avail.trucks);
        r["resources"] = picojson::value(res);
        r["pending_count"] = picojson::value((double)dp->pending_count());
        r["active_count"]  = picojson::value((double)dp->active_count());
        std::vector<std::string> ordered_queue = dp->ordered_queue();
        std::unordered_map<std::string, int> queue_positions;
        for (size_t i = 0; i < ordered_queue.size(); ++i) {
            queue_positions[ordered_queue[i]] = static_cast<int>(i) + 1;
        }
        std::vector<std::string> stable_jobs(known_jobs.begin(), known_jobs.end());
        std::sort(stable_jobs.begin(), stable_jobs.end(),
            [this](const std::string& a, const std::string& b) {
                return dp->job_record(a).submission_seq < dp->job_record(b).submission_seq;
            });

        picojson::array jobs_arr;
        for (const std::string& jid : stable_jobs) {
            try {
                const JobRecord& rec = dp->job_record(jid);
                picojson::object jo;
                jo["job_id"]         = picojson::value(jid);
                std::string st;
                switch(rec.state){
                    case JobState::PENDING:     st="PENDING"; break;
                    case JobState::ACTIVE:      st="ACTIVE"; break;
                    case JobState::INTERRUPTED: st="INTERRUPTED"; break;
                    case JobState::COMPLETED:   st="COMPLETED"; break;
                    case JobState::CANCELLED:   st="CANCELLED"; break;
                }
                jo["state"]          = picojson::value(st);
                jo["work_total"]     = picojson::value((double)rec.job.work_total);
                jo["work_remaining"] = picojson::value((double)rec.work_remaining);
                jo["work_completed"] = picojson::value((double)rec.work_completed);
                jo["arrival_time"]   = picojson::value((double)rec.job.arrival_time);
                jo["priority"]       = picojson::value((double)rec.job.priority);
                jo["submission_seq"] = picojson::value((double)rec.submission_seq);
                auto qpos = queue_positions.find(jid);
                if (qpos != queue_positions.end()) {
                    jo["queue_position"] = picojson::value((double)qpos->second);
                }
                jo["rm_allocated"]   = picojson::value(rec.rm_allocated);
                jo["required_berths"] = picojson::value((double)rec.job.berths);
                jo["required_cranes"] = picojson::value((double)rec.job.cranes);
                jo["required_trucks"] = picojson::value((double)rec.job.trucks);
                bool fulfilled        = dp->crane_count_fulfilled(jid);
                jo["crane_fulfilled"] = picojson::value(fulfilled);
                bool can_complete     = (rec.state == JobState::ACTIVE && fulfilled);
                jo["can_complete"]    = picojson::value(can_complete);
                picojson::array ca;
                for (auto& cid : rec.active_cranes) ca.push_back(picojson::value(cid));
                jo["active_cranes"] = picojson::value(ca);
                jobs_arr.push_back(picojson::value(jo));
            } catch (...) {}
        }
        r["jobs"] = picojson::value(jobs_arr);
        const CraneRegistry& reg = dp->crane_registry();
        picojson::array cr;
        for (const CraneState& cs : reg.all_cranes()) {
            picojson::object co;
            co["crane_id"] = picojson::value(cs.crane_id);
            std::string ss;
            switch(cs.status){ case CraneStatus::AVAILABLE: ss="AVAILABLE"; break;
                case CraneStatus::ASSIGNED: ss="ASSIGNED"; break; case CraneStatus::FAILED: ss="FAILED"; break; }
            co["status"] = picojson::value(ss);
            co["assigned_job"] = picojson::value(cs.assigned_job);
            cr.push_back(picojson::value(co));
        }
        r["cranes"] = picojson::value(cr);
        return r;
    }
};

// ─── Tests ────────────────────────────────────────────────────────────────────

// T1: picojson parse and serialize roundtrip
static bool t_picojson_parse_serialize() {
    std::string in = "{\"cmd\":\"init\",\"berths\":4,\"cranes\":6,\"trucks\":10}";
    picojson::value v;
    std::string err = picojson::parse(v, in);
    CHECK(err.empty());
    CHECK(v.is<picojson::object>());
    const picojson::object& obj = v.get<picojson::object>();
    CHECK_EQ(get_str(obj, "cmd"), "init");
    CHECK_EQ(get_int(obj, "berths"), 4);
    CHECK_EQ(get_int(obj, "cranes"), 6);
    std::string out = v.serialize();
    CHECK(!out.empty());
    // Deserialize the serialized form again
    picojson::value v2;
    err = picojson::parse(v2, out);
    CHECK(err.empty());
    CHECK(v2.is<picojson::object>());
    const picojson::object& obj2 = v2.get<picojson::object>();
    CHECK_EQ(get_str(obj2, "cmd"), "init");
    return true;
}

// T2: Malformed JSON returns error response
static bool t_malformed_json() {
    AdapterSim sim;
    auto r = sim.dispatch("{not valid json");
    CHECK(!get_bool(r, "ok", true));
    std::string e = get_str(r, "error");
    CHECK(!e.empty());
    return true;
}

// T3: Non-object JSON returns error response
static bool t_non_object_json() {
    AdapterSim sim;
    auto r = sim.dispatch("[1,2,3]");
    CHECK(!get_bool(r, "ok", true));
    return true;
}

// T4: Missing cmd field
static bool t_missing_cmd() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2})");
    // Now send a request without cmd
    auto r = sim.dispatch(R"({"berths":2})");
    CHECK(!get_bool(r, "ok", true));
    return true;
}

// T5: Unknown command
static bool t_unknown_cmd() {
    AdapterSim sim;
    auto r = sim.dispatch(R"({"cmd":"fly_to_moon"})");
    CHECK(!get_bool(r, "ok", true));
    std::string e = get_str(r, "error");
    CHECK(e.find("unknown command") != std::string::npos);
    return true;
}

// T6: init before other commands required
static bool t_uninitialised_rejection() {
    AdapterSim sim;
    auto r = sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    CHECK(!get_bool(r, "ok", true));
    std::string e = get_str(r, "error");
    CHECK(e.find("not initialised") != std::string::npos);
    return true;
}

// T7: init with negative berths → PortDispatcher throws → adapter returns error
static bool t_init_negative_berths() {
    AdapterSim sim;
    auto r = sim.dispatch(R"({"cmd":"init","berths":-1,"cranes":2,"trucks":2})");
    CHECK(!get_bool(r, "ok", true));
    return true;
}

// T8: init success + policy field in response
static bool t_init_success() {
    AdapterSim sim;
    auto r = sim.dispatch(R"({"cmd":"init","berths":4,"cranes":3,"trucks":5,"policy":"PRIORITY"})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "policy"), "PRIORITY");
    return true;
}

// T9: register crane duplicate returns error (does not corrupt state)
static bool t_register_crane_duplicate() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    auto r1 = sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    CHECK(get_bool(r1, "ok"));
    auto r2 = sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    CHECK(!get_bool(r2, "ok"));
    // Original crane still in registry
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& crarr = snap.at("cranes").get<picojson::array>();
    CHECK_EQ((int)crarr.size(), 1);
    return true;
}

// T10: submit job → dispatches when cranes available
static bool t_submit_job_dispatches() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");
    auto r = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "state"), "ACTIVE");
    return true;
}

// T11: submit job → stays PENDING when no crane registered
static bool t_submit_job_pending() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    // No cranes registered
    auto r = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "state"), "PENDING");
    return true;
}

// T12: duplicate job_id returns error
static bool t_duplicate_job_id() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto r = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":5,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    CHECK(!get_bool(r, "ok"));
    return true;
}

// T13: submit_job with invalid fields (cranes=0) → error, no state mutation
static bool t_submit_job_bad_cranes() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    auto r = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":0,"trucks":0})");
    CHECK(!get_bool(r, "ok"));
    // No job was registered
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs = snap.at("jobs").get<picojson::array>();
    CHECK(jobs.empty());
    return true;
}

// T14: complete_job on ACTIVE job
static bool t_complete_job_active() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto r = sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "state"), "COMPLETED");
    // Resources released: crane should be available again
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& res = snap.at("resources").get<picojson::object>();
    CHECK_EQ(get_int(res, "avail_cranes"), 4); // back to full
    // Progress fields: COMPLETED job must report work_completed == work_total and work_remaining == 0
    auto& jobs = snap.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs.size(), (size_t)1);
    auto& j1 = jobs[0].get<picojson::object>();
    CHECK_EQ(get_str(j1, "job_id"), "J1");
    CHECK_EQ(get_str(j1, "state"), "COMPLETED");
    CHECK_EQ(get_int(j1, "work_completed"), 10);
    CHECK_EQ(get_int(j1, "work_remaining"), 0);
    return true;
}

// T15: complete_job on PENDING job → error (not ACTIVE)
static bool t_complete_job_not_active() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    // No cranes → job stays PENDING
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto r = sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    CHECK(!get_bool(r, "ok"));
    // Job still exists; PortDispatcher state unchanged
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap, "pending_count"), 1);
    return true;
}

// T16: cancel PENDING job
static bool t_cancel_pending_job() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto r = sim.dispatch(R"({"cmd":"cancel_job","job_id":"J1"})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "state"), "CANCELLED");
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap, "pending_count"), 0);
    return true;
}

// T17: cancel already COMPLETED job → error
static bool t_cancel_completed_job() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    auto r = sim.dispatch(R"({"cmd":"cancel_job","job_id":"J1"})");
    CHECK(!get_bool(r, "ok"));
    return true;
}

// T18: crane_failed on ASSIGNED crane
static bool t_crane_failed_assigned() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    // J1 active with C1 (or C2 depending on order)
    // Fail with work_completed=3, work_remaining=7
    auto r = sim.dispatch(R"({"cmd":"crane_failed","crane_id":"C1","work_completed":3,"work_remaining":7})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "affected_job_id"), "J1");
    // job_interrupted must be true (only one crane was assigned)
    CHECK(get_bool(r, "job_interrupted"));
    return true;
}

// T19: idle_crane_failed
static bool t_idle_crane_failed() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");
    // Only register C1 as IDLE (no jobs yet)
    auto r = sim.dispatch(R"({"cmd":"idle_crane_failed","crane_id":"C1"})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "affected_job_id"), "");
    CHECK(!get_bool(r, "job_interrupted"));
    // Snapshot: C1 is FAILED
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& crarr = snap.at("cranes").get<picojson::array>();
    int failed = 0;
    for (auto& cv : crarr) {
        auto& co = cv.get<picojson::object>();
        if (get_str(co, "crane_id") == "C1" && get_str(co, "status") == "FAILED") ++failed;
    }
    CHECK_EQ(failed, 1);
    return true;
}

// T20: crane_recovered dispatches pending job
static bool t_crane_recovered_dispatches() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    // Fail C1 before submitting a job
    sim.dispatch(R"({"cmd":"idle_crane_failed","crane_id":"C1"})");
    // Submit job (stays PENDING because no available crane)
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto snap1 = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap1, "pending_count"), 1);
    // Recover C1 → should dispatch J1
    auto r = sim.dispatch(R"({"cmd":"crane_recovered","crane_id":"C1"})");
    CHECK(get_bool(r, "ok"));
    CHECK(get_bool(r, "job_dispatched"));
    CHECK_EQ(get_str(r, "dispatched_job_id"), "J1");
    auto snap2 = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap2, "pending_count"), 0);
    CHECK_EQ(get_int(snap2, "active_count"),  1);
    return true;
}

// T21: crane_failed on non-ASSIGNED crane → error, state preserved
static bool t_crane_failed_not_assigned() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    // C1 is AVAILABLE, not ASSIGNED
    auto r = sim.dispatch(R"({"cmd":"crane_failed","crane_id":"C1","work_completed":0,"work_remaining":5})");
    CHECK(!get_bool(r, "ok"));
    // C1 still AVAILABLE
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& crarr = snap.at("cranes").get<picojson::array>();
    int avail = 0;
    for (auto& cv : crarr) {
        auto& co = cv.get<picojson::object>();
        if (get_str(co, "crane_id")=="C1" && get_str(co,"status")=="AVAILABLE") ++avail;
    }
    CHECK_EQ(avail, 1);
    return true;
}

// T22: multiple commands in single session (state persistence)
static bool t_multi_command_session() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");
    // Submit two jobs
    auto r1 = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto r2 = sim.dispatch(R"({"cmd":"submit_job","job_id":"J2","arrival_time":0,"work_total":5,"priority":2,"berths":1,"cranes":1,"trucks":0})");
    CHECK(get_bool(r1, "ok")); CHECK_EQ(get_str(r1, "state"), "ACTIVE");
    CHECK(get_bool(r2, "ok")); CHECK_EQ(get_str(r2, "state"), "ACTIVE");
    // Snapshot should show 2 active, 0 pending
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap, "active_count"), 2);
    CHECK_EQ(get_int(snap, "pending_count"), 0);
    // Complete J1
    sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    // Snapshot should show 1 active
    auto snap2 = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap2, "active_count"), 1);
    // J1 shows COMPLETED in jobs list
    bool j1_completed = false;
    for (auto& jv : snap2.at("jobs").get<picojson::array>()) {
        auto& jo = jv.get<picojson::object>();
        if (get_str(jo, "job_id") == "J1" && get_str(jo, "state") == "COMPLETED")
            j1_completed = true;
    }
    CHECK(j1_completed);
    return true;
}

// T23: snapshot fields are correct before any jobs
static bool t_snapshot_empty() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":3,"cranes":5,"trucks":7})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK(get_bool(snap, "ok"));
    auto& res = snap.at("resources").get<picojson::object>();
    CHECK_EQ(get_int(res, "total_berths"), 3);
    CHECK_EQ(get_int(res, "total_cranes"), 5);
    CHECK_EQ(get_int(res, "total_trucks"), 7);
    CHECK_EQ(get_int(res, "avail_berths"), 3);
    CHECK_EQ(get_int(snap, "pending_count"), 0);
    CHECK_EQ(get_int(snap, "active_count"),  0);
    auto& jobs = snap.at("jobs").get<picojson::array>();
    CHECK(jobs.empty());
    auto& cranes = snap.at("cranes").get<picojson::array>();
    CHECK_EQ((int)cranes.size(), 1);
    return true;
}

// T24: job lifecycle: PENDING → ACTIVE → COMPLETED, resource invariants
static bool t_job_lifecycle_resources() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    // Before job: all available
    auto s0 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& r0 = s0.at("resources").get<picojson::object>();
    CHECK_EQ(get_int(r0,"avail_berths"), 4);
    CHECK_EQ(get_int(r0,"avail_cranes"), 4);
    // Submit job requesting berths=2, cranes=1, trucks=1
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":2,"cranes":1,"trucks":1})");
    auto s1 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& r1 = s1.at("resources").get<picojson::object>();
    // Resources consumed
    CHECK_EQ(get_int(r1,"avail_berths"), 2);
    CHECK_EQ(get_int(r1,"avail_trucks"), 3);
    // Complete job: resources freed
    sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    auto s2 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& r2 = s2.at("resources").get<picojson::object>();
    CHECK_EQ(get_int(r2,"avail_berths"), 4);
    CHECK_EQ(get_int(r2,"avail_cranes"), 4);
    CHECK_EQ(get_int(r2,"avail_trucks"), 4);
    return true;
}

// T25: crane failure then recovery then job completes (no double release)
// PortDispatcher::on_crane_failed does NOT call try_dispatch after re-queuing
// the interrupted job (the failed crane cannot serve; recovery is needed first).
// Re-dispatch happens on crane_recovered or a subsequent submit_job.
static bool t_failure_recovery_complete_no_double_release() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");
    // Submit job requiring 1 crane → dispatched immediately to C1 (lexicographic first)
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    // Fail C1 (assigned) → J1 INTERRUPTED; C2 is available but NOT pumped
    auto fail_r = sim.dispatch(R"({"cmd":"crane_failed","crane_id":"C1","work_completed":3,"work_remaining":7})");
    CHECK(get_bool(fail_r, "ok"));
    CHECK(get_bool(fail_r, "job_interrupted"));
    // J1 must be INTERRUPTED (not yet re-dispatched)
    auto snap1 = sim.dispatch(R"({"cmd":"snapshot"})");
    bool j1_interrupted = false;
    for (auto& jv : snap1.at("jobs").get<picojson::array>()) {
        auto& jo = jv.get<picojson::object>();
        if (get_str(jo,"job_id")=="J1" && get_str(jo,"state")=="INTERRUPTED") j1_interrupted=true;
    }
    CHECK(j1_interrupted);
    CHECK_EQ(get_int(snap1,"pending_count"), 1); // INTERRUPTED counts as pending
    // Recover C1 → try_dispatch pumped → J1 dispatched
    auto rec_r = sim.dispatch(R"({"cmd":"crane_recovered","crane_id":"C1"})");
    CHECK(get_bool(rec_r, "ok"));
    CHECK(get_bool(rec_r, "job_dispatched"));
    CHECK_EQ(get_str(rec_r, "dispatched_job_id"), "J1");
    auto snap2 = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap2,"active_count"),  1);
    CHECK_EQ(get_int(snap2,"pending_count"), 0);
    // Complete J1: RM resources released exactly once
    auto comp_r = sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    CHECK(get_bool(comp_r, "ok"));
    auto snap3 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& r3 = snap3.at("resources").get<picojson::object>();
    CHECK_EQ(get_int(r3,"avail_berths"), 4);
    CHECK_EQ(get_int(r3,"avail_cranes"), 4);
    CHECK_EQ(get_int(snap3,"active_count"), 0);
    // After recovery and completion, J1 must show 10 completed and 0 remaining
    for (auto& jv : snap3.at("jobs").get<picojson::array>()) {
        auto& jo = jv.get<picojson::object>();
        if (get_str(jo, "job_id") == "J1") {
            CHECK_EQ(get_str(jo, "state"), "COMPLETED");
            CHECK_EQ(get_int(jo, "work_completed"), 10);
            CHECK_EQ(get_int(jo, "work_remaining"), 0);
        }
    }
    return true;
}

// T26: work_remaining preserved across interruption
static bool t_work_remaining_preserved() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    // Fail C1: completed 4, remaining 6
    sim.dispatch(R"({"cmd":"crane_failed","crane_id":"C1","work_completed":4,"work_remaining":6})");
    // No other crane → J1 should be INTERRUPTED
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    for (auto& jv : snap.at("jobs").get<picojson::array>()) {
        auto& jo = jv.get<picojson::object>();
        if (get_str(jo,"job_id") == "J1") {
            // work_remaining should reflect the interruption
            CHECK_EQ(get_int(jo,"work_remaining"), 6);
        }
    }
    return true;
}

// T27: shutdown command
static bool t_shutdown() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2})");
    auto r = sim.dispatch(R"({"cmd":"shutdown"})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "msg"), "shutdown");
    return true;
}

// T28: re-init clears state
static bool t_reinit_clears_state() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    // Re-init
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2})");
    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    // jobs should be empty (known_jobs cleared)
    auto& jobs = snap.at("jobs").get<picojson::array>();
    CHECK(jobs.empty());
    // cranes should be empty
    auto& crs = snap.at("cranes").get<picojson::array>();
    CHECK(crs.empty());
    return true;
}

// T29: Exact manual testing scenario for job progress on completion
static bool t_manual_testing_scenario() {
    AdapterSim sim;
    // 1. Initialize dispatcher with 6 berths, 12 cranes, 30 trucks
    sim.dispatch(R"({"cmd":"init","berths":6,"cranes":12,"trucks":30})");
    // 2. Register crane C1
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    // 3. Submit J1 with work_total=10. ACTIVE, work_completed=0, work_remaining=10
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":0})");
    auto snap1 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs1 = snap1.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs1.size(), (size_t)1);
    CHECK_EQ(get_str(jobs1[0].get<picojson::object>(), "state"), "ACTIVE");
    CHECK_EQ(get_int(jobs1[0].get<picojson::object>(), "work_completed"), 0);
    CHECK_EQ(get_int(jobs1[0].get<picojson::object>(), "work_remaining"), 10);
    // 4. Call complete_job for J1. COMPLETED, resources released
    auto r = sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    CHECK(get_bool(r, "ok"));
    CHECK_EQ(get_str(r, "state"), "COMPLETED");
    // 5. Subsequent snapshot reports work_completed=10 and work_remaining=0
    auto snap2 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs2 = snap2.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs2.size(), (size_t)1);
    CHECK_EQ(get_str(jobs2[0].get<picojson::object>(), "state"), "COMPLETED");
    CHECK_EQ(get_int(jobs2[0].get<picojson::object>(), "work_completed"), 10);
    CHECK_EQ(get_int(jobs2[0].get<picojson::object>(), "work_remaining"), 0);
    return true;
}

// T30: Multi-crane premature completion rejected; crane recovery tops up; then completes
static bool t_multicrane_premature_complete_rejected() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");

    auto sub = sim.dispatch(R"({"cmd":"submit_job","job_id":"JOB_NEW","arrival_time":0,"work_total":100,"priority":1,"berths":1,"cranes":2,"trucks":1})");
    CHECK(get_bool(sub, "ok"));
    CHECK_EQ(get_str(sub, "state"), "ACTIVE");

    // Fail C1
    auto f = sim.dispatch(R"({"cmd":"crane_failed","crane_id":"C1","work_completed":30,"work_remaining":70})");
    CHECK(get_bool(f, "ok"));
    CHECK(!get_bool(f, "job_interrupted"));

    // Check snapshot fields
    auto snap1 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs1 = snap1.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs1.size(), (size_t)1);
    auto& j1 = jobs1[0].get<picojson::object>();
    CHECK_EQ(get_str(j1, "state"), "ACTIVE");
    CHECK_EQ(get_int(j1, "required_cranes"), 2);
    CHECK(!get_bool(j1, "crane_fulfilled"));
    CHECK(!get_bool(j1, "can_complete"));

    // Attempt premature complete -> rejected!
    auto comp1 = sim.dispatch(R"({"cmd":"complete_job","job_id":"JOB_NEW"})");
    CHECK(!get_bool(comp1, "ok"));
    CHECK(get_str(comp1, "error").find("Recover the missing crane") != std::string::npos);

    // Recover C1 -> tops up JOB_NEW
    auto rec = sim.dispatch(R"({"cmd":"crane_recovered","crane_id":"C1"})");
    CHECK(get_bool(rec, "ok"));
    CHECK(get_bool(rec, "job_dispatched"));
    CHECK_EQ(get_str(rec, "dispatched_job_id"), "JOB_NEW");

    // Snapshot reflects crane_fulfilled=true, can_complete=true
    auto snap2 = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs2 = snap2.at("jobs").get<picojson::array>();
    auto& j2 = jobs2[0].get<picojson::object>();
    CHECK(get_bool(j2, "crane_fulfilled"));
    CHECK(get_bool(j2, "can_complete"));

    // Now complete succeeds
    auto comp2 = sim.dispatch(R"({"cmd":"complete_job","job_id":"JOB_NEW"})");
    CHECK(get_bool(comp2, "ok"));
    CHECK_EQ(get_str(comp2, "state"), "COMPLETED");

    return true;
}

// T31: snapshot contains all four new metadata fields matching submitted values
static bool t_snapshot_job_metadata_fields() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":8})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");

    auto sub = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":15,"work_total":20,"priority":3,"berths":2,"cranes":2,"trucks":4})");
    CHECK(get_bool(sub, "ok"));

    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK(get_bool(snap, "ok"));

    auto& jobs = snap.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs.size(), (size_t)1);
    auto& jo = jobs[0].get<picojson::object>();

    // 1. Verify presence of all four new fields
    CHECK(jo.find("arrival_time") != jo.end());
    CHECK(jo.find("priority") != jo.end());
    CHECK(jo.find("required_berths") != jo.end());
    CHECK(jo.find("required_trucks") != jo.end());

    // 2. Verify values match the submitted job
    CHECK_EQ(get_int(jo, "arrival_time"), 15);
    CHECK_EQ(get_int(jo, "priority"), 3);
    CHECK_EQ(get_int(jo, "required_berths"), 2);
    CHECK_EQ(get_int(jo, "required_trucks"), 4);

    // 3. Verify all existing fields remain intact with correct values
    CHECK_EQ(get_str(jo, "job_id"), "J1");
    CHECK_EQ(get_str(jo, "state"), "ACTIVE");
    CHECK_EQ(get_int(jo, "work_total"), 20);
    CHECK_EQ(get_int(jo, "work_remaining"), 20);
    CHECK_EQ(get_int(jo, "work_completed"), 0);
    CHECK(get_bool(jo, "rm_allocated"));
    CHECK_EQ(get_int(jo, "required_cranes"), 2);
    CHECK(get_bool(jo, "crane_fulfilled"));
    CHECK(get_bool(jo, "can_complete"));
    auto& active_cranes = jo.at("active_cranes").get<picojson::array>();
    CHECK_EQ(active_cranes.size(), (size_t)2);

    return true;
}

// T32: job metadata fields remain invariant across job lifecycle stages
static bool t_snapshot_job_metadata_lifecycle_invariant() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":4,"cranes":4,"trucks":4})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");

    // 1. ACTIVE state (J1 submitted and dispatched immediately with C1)
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":7,"work_total":10,"priority":2,"berths":1,"cranes":1,"trucks":1})");
    auto s1 = sim.dispatch(R"({"cmd":"snapshot"})");
    picojson::object j1_act;
    for (auto& jv : s1.at("jobs").get<picojson::array>()) {
        if (get_str(jv.get<picojson::object>(), "job_id") == "J1") j1_act = jv.get<picojson::object>();
    }
    CHECK_EQ(get_str(j1_act, "state"), "ACTIVE");
    CHECK_EQ(get_int(j1_act, "arrival_time"), 7);
    CHECK_EQ(get_int(j1_act, "priority"), 2);
    CHECK_EQ(get_int(j1_act, "required_berths"), 1);
    CHECK_EQ(get_int(j1_act, "required_cranes"), 1);
    CHECK_EQ(get_int(j1_act, "required_trucks"), 1);

    // 2. PENDING state (J2 submitted while C1 is busy -> stays PENDING)
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J2","arrival_time":12,"work_total":8,"priority":5,"berths":1,"cranes":1,"trucks":1})");
    auto s2 = sim.dispatch(R"({"cmd":"snapshot"})");
    picojson::object j2_pen;
    for (auto& jv : s2.at("jobs").get<picojson::array>()) {
        if (get_str(jv.get<picojson::object>(), "job_id") == "J2") j2_pen = jv.get<picojson::object>();
    }
    CHECK_EQ(get_str(j2_pen, "state"), "PENDING");
    CHECK_EQ(get_int(j2_pen, "arrival_time"), 12);
    CHECK_EQ(get_int(j2_pen, "priority"), 5);
    CHECK_EQ(get_int(j2_pen, "required_berths"), 1);
    CHECK_EQ(get_int(j2_pen, "required_cranes"), 1);
    CHECK_EQ(get_int(j2_pen, "required_trucks"), 1);

    // 3. CANCELLED state (cancel pending J2)
    sim.dispatch(R"({"cmd":"cancel_job","job_id":"J2"})");
    auto s3 = sim.dispatch(R"({"cmd":"snapshot"})");
    picojson::object j2_can;
    for (auto& jv : s3.at("jobs").get<picojson::array>()) {
        if (get_str(jv.get<picojson::object>(), "job_id") == "J2") j2_can = jv.get<picojson::object>();
    }
    CHECK_EQ(get_str(j2_can, "state"), "CANCELLED");
    CHECK_EQ(get_int(j2_can, "arrival_time"), 12);
    CHECK_EQ(get_int(j2_can, "priority"), 5);
    CHECK_EQ(get_int(j2_can, "required_berths"), 1);
    CHECK_EQ(get_int(j2_can, "required_cranes"), 1);
    CHECK_EQ(get_int(j2_can, "required_trucks"), 1);

    // 4. INTERRUPTED state (C1 fails while working on J1)
    sim.dispatch(R"({"cmd":"crane_failed","crane_id":"C1","work_completed":3,"work_remaining":7})");
    auto s4 = sim.dispatch(R"({"cmd":"snapshot"})");
    picojson::object j1_int;
    for (auto& jv : s4.at("jobs").get<picojson::array>()) {
        if (get_str(jv.get<picojson::object>(), "job_id") == "J1") j1_int = jv.get<picojson::object>();
    }
    CHECK_EQ(get_str(j1_int, "state"), "INTERRUPTED");
    CHECK_EQ(get_int(j1_int, "work_completed"), 3);
    CHECK_EQ(get_int(j1_int, "work_remaining"), 7);
    CHECK_EQ(get_int(j1_int, "arrival_time"), 7);
    CHECK_EQ(get_int(j1_int, "priority"), 2);
    CHECK_EQ(get_int(j1_int, "required_berths"), 1);
    CHECK_EQ(get_int(j1_int, "required_cranes"), 1);
    CHECK_EQ(get_int(j1_int, "required_trucks"), 1);

    // 5. Resumed ACTIVE state (C1 recovered -> J1 re-dispatched)
    sim.dispatch(R"({"cmd":"crane_recovered","crane_id":"C1"})");
    auto s5 = sim.dispatch(R"({"cmd":"snapshot"})");
    picojson::object j1_act2;
    for (auto& jv : s5.at("jobs").get<picojson::array>()) {
        if (get_str(jv.get<picojson::object>(), "job_id") == "J1") j1_act2 = jv.get<picojson::object>();
    }
    CHECK_EQ(get_str(j1_act2, "state"), "ACTIVE");
    CHECK_EQ(get_int(j1_act2, "arrival_time"), 7);
    CHECK_EQ(get_int(j1_act2, "priority"), 2);
    CHECK_EQ(get_int(j1_act2, "required_berths"), 1);
    CHECK_EQ(get_int(j1_act2, "required_cranes"), 1);
    CHECK_EQ(get_int(j1_act2, "required_trucks"), 1);

    // 6. COMPLETED state (complete J1)
    sim.dispatch(R"({"cmd":"complete_job","job_id":"J1"})");
    auto s6 = sim.dispatch(R"({"cmd":"snapshot"})");
    picojson::object j1_cmp;
    for (auto& jv : s6.at("jobs").get<picojson::array>()) {
        if (get_str(jv.get<picojson::object>(), "job_id") == "J1") j1_cmp = jv.get<picojson::object>();
    }
    CHECK_EQ(get_str(j1_cmp, "state"), "COMPLETED");
    CHECK_EQ(get_int(j1_cmp, "work_completed"), 10);
    CHECK_EQ(get_int(j1_cmp, "work_remaining"), 0);
    CHECK_EQ(get_int(j1_cmp, "arrival_time"), 7);
    CHECK_EQ(get_int(j1_cmp, "priority"), 2);
    CHECK_EQ(get_int(j1_cmp, "required_berths"), 1);
    CHECK_EQ(get_int(j1_cmp, "required_cranes"), 1);
    CHECK_EQ(get_int(j1_cmp, "required_trucks"), 1);

    return true;
}

// T33: snapshot with multiple jobs preserves distinct metadata per job
static bool t_snapshot_multiple_jobs_metadata() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":10,"cranes":10,"trucks":20})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C3"})");

    sim.dispatch(R"({"cmd":"submit_job","job_id":"JOB_A","arrival_time":1,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":2})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"JOB_B","arrival_time":10,"work_total":25,"priority":3,"berths":2,"cranes":1,"trucks":5})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"JOB_C","arrival_time":20,"work_total":15,"priority":2,"berths":3,"cranes":1,"trucks":0})");

    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs = snap.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs.size(), (size_t)3);

    int matched = 0;
    for (auto& jv : jobs) {
        auto& jo = jv.get<picojson::object>();
        std::string jid = get_str(jo, "job_id");
        if (jid == "JOB_A") {
            CHECK_EQ(get_int(jo, "arrival_time"), 1);
            CHECK_EQ(get_int(jo, "priority"), 1);
            CHECK_EQ(get_int(jo, "required_berths"), 1);
            CHECK_EQ(get_int(jo, "required_cranes"), 1);
            CHECK_EQ(get_int(jo, "required_trucks"), 2);
            ++matched;
        } else if (jid == "JOB_B") {
            CHECK_EQ(get_int(jo, "arrival_time"), 10);
            CHECK_EQ(get_int(jo, "priority"), 3);
            CHECK_EQ(get_int(jo, "required_berths"), 2);
            CHECK_EQ(get_int(jo, "required_cranes"), 1);
            CHECK_EQ(get_int(jo, "required_trucks"), 5);
            ++matched;
        } else if (jid == "JOB_C") {
            CHECK_EQ(get_int(jo, "arrival_time"), 20);
            CHECK_EQ(get_int(jo, "priority"), 2);
            CHECK_EQ(get_int(jo, "required_berths"), 3);
            CHECK_EQ(get_int(jo, "required_cranes"), 1);
            CHECK_EQ(get_int(jo, "required_trucks"), 0);
            ++matched;
        }
    }
    CHECK_EQ(matched, 3);
    return true;
}

// Snapshot job order is stable by submission, while queue_position reflects
// the live dispatch policy independently of the JSON array order.
static bool t_snapshot_queue_order_is_authoritative_and_deterministic() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2,"policy":"SJF","batch_mode":true})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"LONG","arrival_time":99,"work_total":30,"priority":5,"berths":1,"cranes":1,"trucks":1})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"SHORT_A","arrival_time":0,"work_total":10,"priority":5,"berths":1,"cranes":1,"trucks":1})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"SHORT_B","arrival_time":1,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":1})");

    auto first = sim.dispatch(R"({"cmd":"snapshot"})");
    auto second = sim.dispatch(R"({"cmd":"snapshot"})");
    auto& jobs1 = first.at("jobs").get<picojson::array>();
    auto& jobs2 = second.at("jobs").get<picojson::array>();
    CHECK_EQ(jobs1.size(), (size_t)3);
    CHECK_EQ(jobs2.size(), (size_t)3);

    const char* submitted[] = {"LONG", "SHORT_A", "SHORT_B"};
    const int queue_positions[] = {3, 1, 2};
    for (size_t i = 0; i < jobs1.size(); ++i) {
        auto& a = jobs1[i].get<picojson::object>();
        auto& b = jobs2[i].get<picojson::object>();
        CHECK_EQ(get_str(a, "job_id"), std::string(submitted[i]));
        CHECK_EQ(get_str(b, "job_id"), std::string(submitted[i]));
        CHECK_EQ(get_int(a, "submission_seq"), static_cast<int>(i));
        CHECK_EQ(get_int(a, "queue_position"), queue_positions[i]);
    }
    return true;
}

// ─── main ─────────────────────────────────────────────────────────────────────


// ─── Batch dispatch tests ─────────────────────────────────────────────────────

static bool t_batch_mode_init_and_submit_stays_pending() {
    AdapterSim sim;
    auto r1 = sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2,"policy":"FCFS","batch_mode":true})");
    CHECK(r1.at("ok").get<bool>());
    CHECK(r1.at("batch_mode").get<bool>());

    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");

    auto r2 = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":1})");
    CHECK(r2.at("ok").get<bool>());
    CHECK_EQ(get_str(r2, "state"), "PENDING");

    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK(snap.at("batch_mode").get<bool>());
    CHECK_EQ(get_int(snap, "pending_count"), 1);
    CHECK_EQ(get_int(snap, "active_count"), 0);
    return true;
}

static bool t_batch_mode_dispatch_batch() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2,"policy":"PRIORITY","batch_mode":true})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C2"})");

    sim.dispatch(R"({"cmd":"submit_job","job_id":"J_LOW","arrival_time":0,"work_total":10,"priority":5,"berths":1,"cranes":1,"trucks":1})");
    sim.dispatch(R"({"cmd":"submit_job","job_id":"J_HIGH","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":1})");

    auto snap1 = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap1, "pending_count"), 2);
    CHECK_EQ(get_int(snap1, "active_count"), 0);

    auto r = sim.dispatch(R"({"cmd":"dispatch_batch"})");
    CHECK(r.at("ok").get<bool>());
    CHECK_EQ(get_int(r, "count"), 2);
    auto jobs_arr = r.at("dispatched_jobs").get<picojson::array>();
    CHECK_EQ(static_cast<int>(jobs_arr.size()), 2);
    // Highest priority (lowest numeric value) should be first
    CHECK_EQ(jobs_arr[0].get<std::string>(), "J_HIGH");
    CHECK_EQ(jobs_arr[1].get<std::string>(), "J_LOW");

    auto snap2 = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK_EQ(get_int(snap2, "pending_count"), 0);
    CHECK_EQ(get_int(snap2, "active_count"), 2);
    return true;
}

static bool t_batch_mode_dynamic_toggle() {
    AdapterSim sim;
    sim.dispatch(R"({"cmd":"init","berths":2,"cranes":2,"trucks":2,"policy":"FCFS","batch_mode":false})");
    sim.dispatch(R"({"cmd":"register_crane","crane_id":"C1"})");

    // Toggle batch mode on
    auto r = sim.dispatch(R"({"cmd":"set_batch_mode","batch_mode":true})");
    CHECK(r.at("ok").get<bool>());
    CHECK(r.at("batch_mode").get<bool>());

    auto snap = sim.dispatch(R"({"cmd":"snapshot"})");
    CHECK(snap.at("batch_mode").get<bool>());

    // Submit job while in batch mode -> stays PENDING
    auto r_sub = sim.dispatch(R"({"cmd":"submit_job","job_id":"J1","arrival_time":0,"work_total":10,"priority":1,"berths":1,"cranes":1,"trucks":1})");
    CHECK_EQ(get_str(r_sub, "state"), "PENDING");

    // Dispatch
    auto r_disp = sim.dispatch(R"({"cmd":"dispatch_batch"})");
    CHECK_EQ(get_int(r_disp, "count"), 1);
    CHECK_EQ(r_disp.at("dispatched_jobs").get<picojson::array>()[0].get<std::string>(), "J1");

    return true;
}

#include <fstream>
#include <cstdlib>

static bool t_actual_binary_batch_dispatch_subprocess() {
    // Write test input script
    std::ofstream in("test_batch_proc_in.jsonl");
    in << "{\"cmd\":\"init\",\"berths\":2,\"cranes\":2,\"trucks\":2,\"policy\":\"FCFS\",\"batch_mode\":true}\n";
    in << "{\"cmd\":\"register_crane\",\"crane_id\":\"C1\"}\n";
    in << "{\"cmd\":\"submit_job\",\"job_id\":\"J1\",\"arrival_time\":0,\"work_total\":10,\"priority\":1,\"berths\":1,\"cranes\":1,\"trucks\":1}\n";
    in << "{\"cmd\":\"snapshot\"}\n";
    in << "{\"cmd\":\"dispatch_batch\"}\n";
    in << "{\"cmd\":\"snapshot\"}\n";
    in << "{\"cmd\":\"shutdown\"}\n";
    in.close();

    int rc = std::system(".\\port_json_iface.exe < test_batch_proc_in.jsonl > test_batch_proc_out.jsonl");
    CHECK_EQ(rc, 0);

    std::ifstream out("test_batch_proc_out.jsonl");
    std::vector<picojson::object> responses;
    std::string line;
    while (std::getline(out, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        picojson::value v;
        std::string err = picojson::parse(v, line);
        CHECK(err.empty());
        CHECK(v.is<picojson::object>());
        responses.push_back(v.get<picojson::object>());
    }
    out.close();

    std::remove("test_batch_proc_in.jsonl");
    std::remove("test_batch_proc_out.jsonl");

    CHECK_EQ(static_cast<int>(responses.size()), 7);
    // 0: init
    CHECK(responses[0].at("ok").get<bool>());
    CHECK(responses[0].at("batch_mode").get<bool>());
    // 1: register_crane
    CHECK(responses[1].at("ok").get<bool>());
    // 2: submit_job -> state PENDING
    CHECK(responses[2].at("ok").get<bool>());
    CHECK_EQ(get_str(responses[2], "state"), "PENDING");
    // 3: snapshot before dispatch
    CHECK_EQ(get_int(responses[3], "pending_count"), 1);
    CHECK_EQ(get_int(responses[3], "active_count"), 0);
    auto& pending_jobs = responses[3].at("jobs").get<picojson::array>();
    CHECK_EQ(pending_jobs.size(), (size_t)1);
    auto& pending_job = pending_jobs[0].get<picojson::object>();
    CHECK_EQ(get_int(pending_job, "submission_seq"), 0);
    CHECK_EQ(get_int(pending_job, "queue_position"), 1);
    // 4: dispatch_batch -> 1 job
    CHECK(responses[4].at("ok").get<bool>());
    CHECK_EQ(get_int(responses[4], "count"), 1);
    // 5: snapshot after dispatch
    CHECK_EQ(get_int(responses[5], "pending_count"), 0);
    CHECK_EQ(get_int(responses[5], "active_count"), 1);
    // 6: shutdown
    CHECK(responses[6].at("ok").get<bool>());

    return true;
}

int main() {
    run_test("picojson_parse_serialize",        t_picojson_parse_serialize);
    run_test("malformed_json",                  t_malformed_json);
    run_test("non_object_json",                 t_non_object_json);
    run_test("missing_cmd",                     t_missing_cmd);
    run_test("unknown_cmd",                     t_unknown_cmd);
    run_test("uninitialised_rejection",         t_uninitialised_rejection);
    run_test("init_negative_berths",            t_init_negative_berths);
    run_test("init_success",                    t_init_success);
    run_test("register_crane_duplicate",        t_register_crane_duplicate);
    run_test("submit_job_dispatches",           t_submit_job_dispatches);
    run_test("submit_job_pending",              t_submit_job_pending);
    run_test("duplicate_job_id",               t_duplicate_job_id);
    run_test("submit_job_bad_cranes",          t_submit_job_bad_cranes);
    run_test("complete_job_active",            t_complete_job_active);
    run_test("complete_job_not_active",        t_complete_job_not_active);
    run_test("cancel_pending_job",             t_cancel_pending_job);
    run_test("cancel_completed_job",           t_cancel_completed_job);
    run_test("crane_failed_assigned",          t_crane_failed_assigned);
    run_test("idle_crane_failed",              t_idle_crane_failed);
    run_test("crane_recovered_dispatches",     t_crane_recovered_dispatches);
    run_test("crane_failed_not_assigned",      t_crane_failed_not_assigned);
    run_test("multi_command_session",          t_multi_command_session);
    run_test("snapshot_empty",                 t_snapshot_empty);
    run_test("job_lifecycle_resources",        t_job_lifecycle_resources);
    run_test("failure_recovery_no_dbl_release",t_failure_recovery_complete_no_double_release);
    run_test("work_remaining_preserved",       t_work_remaining_preserved);
    run_test("shutdown",                       t_shutdown);
    run_test("reinit_clears_state",            t_reinit_clears_state);
    run_test("manual_testing_scenario",        t_manual_testing_scenario);
    run_test("multicrane_premature_complete_rejected", t_multicrane_premature_complete_rejected);
    run_test("snapshot_job_metadata_fields",            t_snapshot_job_metadata_fields);
    run_test("snapshot_job_metadata_lifecycle_invariant", t_snapshot_job_metadata_lifecycle_invariant);
    run_test("snapshot_multiple_jobs_metadata",         t_snapshot_multiple_jobs_metadata);
    run_test("snapshot_queue_order_authoritative",      t_snapshot_queue_order_is_authoritative_and_deterministic);
    run_test("batch_mode_init_and_submit_stays_pending", t_batch_mode_init_and_submit_stays_pending);
    run_test("batch_mode_dispatch_batch",               t_batch_mode_dispatch_batch);
    run_test("batch_mode_dynamic_toggle",               t_batch_mode_dynamic_toggle);
    run_test("actual_binary_batch_dispatch_subprocess", t_actual_binary_batch_dispatch_subprocess);

    std::cerr << "\n=== Results: " << g_passed << "/" << g_total << " passed";
    if (g_failed > 0) std::cerr << ", " << g_failed << " FAILED";
    std::cerr << " ===\n";

    return g_failed == 0 ? 0 : 1;
}
