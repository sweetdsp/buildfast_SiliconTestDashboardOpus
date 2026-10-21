"""Curated "ground truth" firmware defects driving the mock runs.

Each defect is active from build `introduced` up to (not including) build
`fixed` (1-based; None = still open). While active it can:
  - fail specific test cases        ("tests":  domain/feature/count)
  - fail gating tests, blocking their sub-suites ("gating": domain/feature/blocked)
  - crash MTBF DUTs                 ("crash":  expected occurrences per cycle)
  - shift KPI metrics               ("kpi":    metric -> delta)

These planted scenarios give the analysis engine real signal to find:
  * >10 unique crash signatures early on, converging to 3 by RC3
  * BTFW-4012 (LC3 NULL deref) gating ~45 LE Audio tests
  * BTFW-4031 scheduler starvation: LE Audio failure spike + A2DP latency breach
  * DUT-02 (EVT2 board) crashing ~3x more than its peers (see catalog)
Minor defects and single-instance failures are added procedurally on top.
"""

HCI, PHONE = "HCI_REGRESSION", "PHONE_TESTING"


def _t(domain, feature, count):
    return {"domain": domain, "feature": feature, "count": count}


def _g(domain, feature, blocked):
    return {"domain": domain, "feature": feature, "blocked": blocked}


def _c(expected, duts=None, stack=(), manual_recovery=0.0):
    return {"expected": expected, "duts": duts, "stack": list(stack), "manual_recovery": manual_recovery}


DEFECTS = [
    # ---- Open at RC3 -------------------------------------------------------
    {"jira_id": "BTFW-4012", "signature": "NullPointerInLC3CodecBuffer",
     "title": "LC3 codec frame buffer NULL dereference on CIS re-establishment",
     "subsystem": "AUDIO_CODEC", "severity": "Blocker", "introduced": 2, "fixed": None,
     "tests": [_t(HCI, "LE_Audio_CIS_BIS", 6), _t(PHONE, "LE_Audio_Unicast", 3)],
     "gating": [_g(HCI, "LE_Audio_CIS_BIS", 45), _g(PHONE, "LE_Audio_Unicast", 10)],
     "crash": _c(8, ["DUT-02", "DUT-05", "DUT-09", "DUT-14"],
                 ["lc3_decode_frame+0x1a4", "iso_rx_sdu_complete+0x88", "cis_rx_handler+0x3c",
                  "ll_isr_dispatch+0x110"])},
    {"jira_id": "BTFW-4018", "signature": "LinkManagerTimeoutOnRoleSwitch",
     "title": "LMP response timeout during role switch with encrypted ACL",
     "subsystem": "LMP", "severity": "Critical", "introduced": 1, "fixed": None,
     "tests": [_t(HCI, "Role_Switch", 8), _t(HCI, "BR_EDR_Connections", 4),
               _t(PHONE, "Multi_Device_Connection", 3)],
     "crash": _c(4, ["DUT-01", "DUT-07"],
                 ["lmp_role_switch_fsm+0x2f0", "lmp_timer_expiry+0x54", "lc_sched_tick+0x9c"])},
    {"jira_id": "BTFW-4022", "signature": "HCI_Reset_Hang_Post_A2DP_Stream",
     "title": "Controller hangs on HCI_Reset issued right after A2DP stream teardown",
     "subsystem": "HCI", "severity": "Critical", "introduced": 4, "fixed": None,
     "tests": [_t(HCI, "HCI_Reset_Init", 6), _t(PHONE, "Airplane_Toggle_Recovery", 4)],
     "crash": _c(2, ["DUT-11"],
                 ["hci_cmd_reset+0x40", "acl_flush_pending+0x1d8", "mutex_lock_timeout+0x22"])},
    {"jira_id": "BTFW-4031", "signature": "ISO slot starvation under concurrent A2DP + LE Audio",
     "title": "Scheduler starves ISO events when A2DP ACL and LE Audio CIS run concurrently",
     "subsystem": "BASEBAND", "severity": "Critical", "introduced": 5, "fixed": None,
     "tests": [_t(HCI, "LE_Audio_CIS_BIS", 22), _t(HCI, "SCO_eSCO", 4),
               _t(PHONE, "LE_Audio_Unicast", 6), _t(PHONE, "A2DP_Streaming", 5),
               _t(PHONE, "LE_Audio_Broadcast", 4)],
     "kpi": {"A2DP_Latency_ms": 38, "A2DP_Dropouts_per_hr": 0.8, "LE_Audio_Latency_ms": 5}},
    {"jira_id": "BTFW-4027", "signature": "GATT DB hash not regenerated after service change",
     "title": "Robust caching: database hash stale after Service Changed indication",
     "subsystem": "HOST_STACK", "severity": "Critical", "introduced": 3, "fixed": None,
     "tests": [_t(HCI, "GATT_ATT", 8), _t(PHONE, "BLE_Wearables_GATT", 4)],
     "gating": [_g(HCI, "GATT_ATT", 12)]},
    {"jira_id": "BTFW-4035", "signature": "Path loss report stuck after PHY update",
     "title": "LE Power Control path-loss reporting stops after PHY update to Coded",
     "subsystem": "LINK_LAYER", "severity": "Medium", "introduced": 6, "fixed": None,
     "tests": [_t(HCI, "LE_Power_Control", 9), _t(HCI, "LE_2M_Coded_PHY", 3)],
     "kpi": {"Power_LE_Audio_Stream_mW": 0.3}},
    {"jira_id": "BTFW-4040", "signature": "LE 2M throughput drop with DLE 251",
     "title": "LE 2M PHY throughput regression when data length is 251 octets",
     "subsystem": "PHY_RF", "severity": "Medium", "introduced": 6, "fixed": None,
     "tests": [_t(HCI, "LE_Data_Length_Ext", 5)],
     "kpi": {"LE_2M_Throughput_kbps": -110}},

    # ---- Functional defects fixed during the programme ---------------------
    {"jira_id": "BTFW-4009", "signature": "AFH map not applied after Wi-Fi channel change",
     "title": "AFH channel map not refreshed when Wi-Fi moves channel", "subsystem": "COEX",
     "severity": "Critical", "introduced": 1, "fixed": 6,
     "tests": [_t(HCI, "AFH", 10), _t(PHONE, "BT_WiFi_Coex", 8)],
     "kpi": {"Packet_Error_Rate_percent": 0.6}},
    {"jira_id": "BTFW-3988", "signature": "Sniff subrating exit latency exceeds spec",
     "title": "Sniff subrating exit takes > 2 sniff intervals", "subsystem": "POWER_MGMT",
     "severity": "Medium", "introduced": 1, "fixed": 4,
     "tests": [_t(HCI, "Sniff_Mode", 7)], "kpi": {"Power_Sniff_Idle_mW": 0.35}},
    {"jira_id": "BTFW-4001", "signature": "SSP numeric comparison fails for KeyboardOnly IO cap",
     "title": "SSP falls back incorrectly when peer IO capability is KeyboardOnly",
     "subsystem": "SECURITY", "severity": "Critical", "introduced": 1, "fixed": 3,
     "tests": [_t(HCI, "Secure_Simple_Pairing", 12), _t(PHONE, "Pairing_Bonding", 6)],
     "gating": [_g(HCI, "Secure_Simple_Pairing", 15)]},
    {"jira_id": "BTFW-4015", "signature": "Extended adv set removal leaks controller memory",
     "title": "Memory leak when removing extended advertising sets", "subsystem": "LINK_LAYER",
     "severity": "Medium", "introduced": 2, "fixed": 5,
     "tests": [_t(HCI, "LE_Advertising", 8), _t(HCI, "LE_Periodic_Adv", 5)]},
    {"jira_id": "BTFW-3996", "signature": "Page scan window misconfigured in interlaced mode",
     "title": "Interlaced page scan uses wrong window, connections time out",
     "subsystem": "BASEBAND", "severity": "Critical", "introduced": 1, "fixed": 4,
     "tests": [_t(HCI, "BR_EDR_Inquiry_Paging", 9), _t(PHONE, "HFP_Calls", 4)],
     "gating": [_g(HCI, "BR_EDR_Inquiry_Paging", 14)]},

    # ---- Crash defects (MTBF) fixed during the programme -------------------
    {"jira_id": "BTFW-3961", "signature": "WatchdogResetInBasebandScheduler",
     "title": "Watchdog reset: baseband scheduler loops on overlapping reservations",
     "subsystem": "BASEBAND", "severity": "Blocker", "introduced": 1, "fixed": 3,
     "tests": [_t(HCI, "SCO_eSCO", 5)],
     "crash": _c(6, None, ["sched_resolve_conflict+0x7c", "sched_insert+0x1f0", "wdt_isr+0x10"])},
    {"jira_id": "BTFW-3965", "signature": "HeapCorruptionInACLReassembly",
     "title": "Heap corruption reassembling fragmented ACL packets above 1021 bytes",
     "subsystem": "HCI", "severity": "Critical", "introduced": 1, "fixed": 4,
     "tests": [_t(HCI, "HCI_Flow_Control", 9)],
     "crash": _c(4, None, ["acl_reassemble+0x134", "heap_free+0x48", "hci_acl_rx+0x90"])},
    {"jira_id": "BTFW-3970", "signature": "AssertInLLConnectionUpdate",
     "title": "Assert in LL connection update when instant collides with channel map update",
     "subsystem": "LINK_LAYER", "severity": "Critical", "introduced": 1, "fixed": 5,
     "tests": [_t(HCI, "LE_Connection", 10)],
     "crash": _c(5, None, ["ll_conn_update_instant+0x88", "assert_handler+0x14", "ll_evt_prepare+0x2c4"])},
    {"jira_id": "BTFW-3972", "signature": "StackOverflowInSMPPairing",
     "title": "Stack overflow in SMP P-256 computation on low-priority task",
     "subsystem": "SECURITY", "severity": "Critical", "introduced": 1, "fixed": 3,
     "tests": [_t(HCI, "LE_Secure_Connections", 6)],
     "crash": _c(3, ["DUT-03", "DUT-04", "DUT-08"],
                 ["ecc_p256_mul+0x3a0", "smp_dhkey_check+0x74", "task_stack_guard+0x08"])},
    {"jira_id": "BTFW-3977", "signature": "RFCalibrationTimeoutAfterSleep",
     "title": "RF calibration times out on wake from deep sleep", "subsystem": "PHY_RF",
     "severity": "Critical", "introduced": 1, "fixed": 6,
     "crash": _c(4, ["DUT-02", "DUT-06", "DUT-10", "DUT-13"],
                 ["rf_cal_run+0x15c", "pm_exit_deep_sleep+0x60", "radio_init+0x24"])},
    {"jira_id": "BTFW-3980", "signature": "DeadlockInCoexArbiter",
     "title": "Deadlock between coex arbiter and Wi-Fi grant callback", "subsystem": "COEX",
     "severity": "Blocker", "introduced": 1, "fixed": 7,
     "tests": [_t(PHONE, "BT_WiFi_Coex", 5)],
     "crash": _c(3, None, ["coex_arbiter_lock+0x30", "wlan_grant_cb+0x5c", "os_mutex_wait+0x18"],
                 manual_recovery=0.5)},
    {"jira_id": "BTFW-3983", "signature": "ISOChannelDesyncOnCISTeardown",
     "title": "ISO channel loses sync when one CIS of a CIG is torn down", "subsystem": "LINK_LAYER",
     "severity": "Critical", "introduced": 1, "fixed": 6,
     "tests": [_t(HCI, "LE_Audio_CIS_BIS", 8)],
     "crash": _c(5, None, ["cig_remove_cis+0xd4", "iso_sync_lost+0x40", "ll_isr_dispatch+0x110"])},
    {"jira_id": "BTFW-3985", "signature": "DoubleFreeInGATTCacheFlush",
     "title": "Double free flushing GATT client cache on disconnect", "subsystem": "HOST_STACK",
     "severity": "Critical", "introduced": 1, "fixed": 2,
     "crash": _c(3, None, ["gatt_cache_flush+0x58", "heap_free+0x48", "l2cap_disconnect_cb+0x34"])},
    {"jira_id": "BTFW-3990", "signature": "PowerMgrWakeupRaceOnSniffExit",
     "title": "Race between power manager wake-up and sniff exit request", "subsystem": "POWER_MGMT",
     "severity": "Critical", "introduced": 1, "fixed": 5,
     "crash": _c(4, None, ["pm_wakeup_handler+0x9c", "lmp_sniff_exit+0x48", "irq_dispatch+0x20"])},
    {"jira_id": "BTFW-3993", "signature": "HardFaultInAFHChannelMapUpdate",
     "title": "Hard fault applying AFH channel map with fewer than 20 channels", "subsystem": "PHY_RF",
     "severity": "Critical", "introduced": 1, "fixed": 4,
     "crash": _c(2, None, ["afh_apply_map+0x64", "hop_seq_regen+0x1b0", "hardfault_handler+0x00"])},
    {"jira_id": "BTFW-4029", "signature": "UseAfterFreeInESCOTeardown",
     "title": "Use-after-free when eSCO link is torn down during codec renegotiation",
     "subsystem": "BASEBAND", "severity": "Critical", "introduced": 5, "fixed": 7,
     "tests": [_t(PHONE, "HFP_Calls", 4)],
     "crash": _c(3, None, ["esco_link_free+0x2c", "codec_reneg_cb+0x80", "sched_remove+0x44"])},
]
