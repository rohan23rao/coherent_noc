"""The two protocol tables, transcribed from SPEC.md by hand.

This is one of the project's two independent renderings of the same tables --
the other is `rtl/l1/l1_coh_fsm.sv` and `rtl/l2/dir_coh_fsm.sv`. The table
tests compare them cell by cell, which is a real check and not a tautology: a
transcription slip in one is very unlikely to be mirrored in the other.

It lives under models/ rather than inside a test file because two consumers
need it and they must not drift apart:

  * `test_protocol_l1_table.py` / `test_protocol_dir_table.py` check the RTL
    against it, exhaustively, including that every blank cell raises `illegal`.
  * `models/coverage.py` uses its keys as the legal-bin list. "Every legal
    (state, event) pair was exercised" only means something if the definition
    of legal is the same one the RTL was checked against.

Names are kept exactly as the two test files had them, so the diff that moved
them here changes no cell.
"""

# --- state encodings, matching coh_pkg::l1_state_e -------------------------
I, S, E, M, IS_D, IM_AD, IM_A, SM_AD, SM_A, MI_A, EI_A, SI_A, II_A = range(13)
STATE_NAMES = ["I", "S", "E", "M", "IS_D", "IM_AD", "IM_A", "SM_AD", "SM_A",
               "MI_A", "EI_A", "SI_A", "II_A"]

# --- event encodings, matching coh_pkg::l1_event_e -------------------------
(LOAD, STORE, EVICT, FWD_GETS, FWD_GETM, INV, PUT_ACK,
 DATA_E, DATA_A0, DATA_AGT0, DATA_OWNER, INV_ACK) = range(12)
EVENT_NAMES = ["Load", "Store", "Evict", "Fwd-GetS", "Fwd-GetM", "Inv",
               "Put-Ack", "DataE", "Data[ack=0]", "Data[ack>0]",
               "Data-owner", "Inv-Ack"]

# Action bit names, in declaration order within coh_pkg::l1_action_t. The
# struct is packed, so the first-declared field is the MSB.
ACTION_FIELDS = [
    "illegal", "stall", "hit", "send_gets", "send_getm", "send_puts",
    "send_putm", "send_pute", "send_inv_ack", "send_data_req",
    "send_data_dir", "ack_dec", "ack_add", "fill_data", "complete",
]

STALL = {"stall"}


def A(*names):
    return set(names)


# Independent transcription of the table in SPEC.md.
# Key: (state, event) -> (next_state, action set). Absent means blank/illegal.
L1_TABLE = {
    (I, LOAD):            (IS_D,  A("send_gets")),
    (I, STORE):           (IM_AD, A("send_getm")),

    (IS_D, LOAD):         (IS_D,  STALL),
    (IS_D, STORE):        (IS_D,  STALL),
    (IS_D, EVICT):        (IS_D,  STALL),
    (IS_D, FWD_GETS):     (IS_D,  STALL),
    (IS_D, FWD_GETM):     (IS_D,  STALL),
    (IS_D, INV):          (IS_D,  STALL),
    (IS_D, DATA_E):       (E,     A("fill_data", "complete")),
    (IS_D, DATA_A0):      (S,     A("fill_data", "complete")),
    (IS_D, DATA_OWNER):   (S,     A("fill_data", "complete")),

    (IM_AD, LOAD):        (IM_AD, STALL),
    (IM_AD, STORE):       (IM_AD, STALL),
    (IM_AD, EVICT):       (IM_AD, STALL),
    (IM_AD, FWD_GETS):    (IM_AD, STALL),
    (IM_AD, FWD_GETM):    (IM_AD, STALL),
    (IM_AD, INV):         (IM_AD, STALL),
    (IM_AD, DATA_A0):     (M,     A("fill_data", "complete")),
    (IM_AD, DATA_AGT0):   (IM_A,  A("fill_data", "ack_add")),
    (IM_AD, DATA_OWNER):  (M,     A("fill_data", "complete")),
    (IM_AD, INV_ACK):     (IM_AD, A("ack_dec")),

    (IM_A, LOAD):         (IM_A,  STALL),
    (IM_A, STORE):        (IM_A,  STALL),
    (IM_A, EVICT):        (IM_A,  STALL),
    (IM_A, FWD_GETS):     (IM_A,  STALL),
    (IM_A, FWD_GETM):     (IM_A,  STALL),
    (IM_A, INV):          (IM_A,  STALL),
    (IM_A, INV_ACK):      (IM_A,  A("ack_dec")),

    (S, LOAD):            (S,     A("hit")),
    (S, STORE):           (SM_AD, A("send_getm")),
    (S, EVICT):           (SI_A,  A("send_puts")),
    (S, INV):             (I,     A("send_inv_ack")),

    (SM_AD, LOAD):        (SM_AD, A("hit")),
    (SM_AD, STORE):       (SM_AD, STALL),
    (SM_AD, EVICT):       (SM_AD, STALL),
    (SM_AD, FWD_GETS):    (SM_AD, STALL),
    (SM_AD, FWD_GETM):    (SM_AD, STALL),
    (SM_AD, INV):         (IM_AD, A("send_inv_ack")),
    (SM_AD, DATA_A0):     (M,     A("fill_data", "complete")),
    (SM_AD, DATA_AGT0):   (SM_A,  A("fill_data", "ack_add")),
    (SM_AD, DATA_OWNER):  (M,     A("fill_data", "complete")),
    (SM_AD, INV_ACK):     (SM_AD, A("ack_dec")),

    (SM_A, LOAD):         (SM_A,  A("hit")),
    (SM_A, STORE):        (SM_A,  STALL),
    (SM_A, EVICT):        (SM_A,  STALL),
    (SM_A, FWD_GETS):     (SM_A,  STALL),
    (SM_A, FWD_GETM):     (SM_A,  STALL),
    (SM_A, INV_ACK):      (SM_A,  A("ack_dec")),

    (E, LOAD):            (E,     A("hit")),
    (E, STORE):           (M,     A("hit")),
    (E, EVICT):           (EI_A,  A("send_pute")),
    (E, FWD_GETS):        (S,     A("send_data_req", "send_data_dir")),
    (E, FWD_GETM):        (I,     A("send_data_req")),

    (M, LOAD):            (M,     A("hit")),
    (M, STORE):           (M,     A("hit")),
    (M, EVICT):           (MI_A,  A("send_putm")),
    (M, FWD_GETS):        (S,     A("send_data_req", "send_data_dir")),
    (M, FWD_GETM):        (I,     A("send_data_req")),

    (MI_A, LOAD):         (MI_A,  STALL),
    (MI_A, STORE):        (MI_A,  STALL),
    (MI_A, EVICT):        (MI_A,  STALL),
    (MI_A, FWD_GETS):     (SI_A,  A("send_data_req", "send_data_dir")),
    (MI_A, FWD_GETM):     (II_A,  A("send_data_req")),
    (MI_A, PUT_ACK):      (I,     A("complete")),

    (EI_A, LOAD):         (EI_A,  STALL),
    (EI_A, STORE):        (EI_A,  STALL),
    (EI_A, EVICT):        (EI_A,  STALL),
    (EI_A, FWD_GETS):     (SI_A,  A("send_data_req", "send_data_dir")),
    (EI_A, FWD_GETM):     (II_A,  A("send_data_req")),
    (EI_A, PUT_ACK):      (I,     A("complete")),

    (SI_A, LOAD):         (SI_A,  STALL),
    (SI_A, STORE):        (SI_A,  STALL),
    (SI_A, EVICT):        (SI_A,  STALL),
    (SI_A, INV):          (II_A,  A("send_inv_ack")),
    (SI_A, PUT_ACK):      (I,     A("complete")),

    (II_A, LOAD):         (II_A,  STALL),
    (II_A, STORE):        (II_A,  STALL),
    (II_A, EVICT):        (II_A,  STALL),
    (II_A, PUT_ACK):      (I,     A("complete")),
}


# coh_pkg::dir_state_e
DI, DS, DE, DM, SD = range(5)
DIR_STATE_NAMES = ["I", "S", "E", "M", "S_D"]

# coh_pkg::dir_event_e
(GETS, GETM, PUTS_NOT_LAST, PUTS_LAST, PUTM_OWNER, PUTM_NON_OWNER,
 PUTE_OWNER, PUTE_NON_OWNER, DATA) = range(9)
DIR_EVENT_NAMES = ["GetS", "GetM", "PutS(not last)", "PutS(last)",
               "PutM from owner", "PutM non-owner", "PutE from owner",
               "PutE non-owner", "Data"]

DIR_ACTION_FIELDS = [
    "illegal", "stall", "send_data", "send_data_e", "send_inv_others",
    "send_fwd_gets", "send_fwd_getm", "send_put_ack", "add_sharer",
    "remove_sharer", "clear_sharers", "sharers_owner_req", "set_owner_req",
    "clear_owner", "copy_data_l2",
]

ACK = {"send_put_ack"}
REMOVE_ACK = {"remove_sharer", "send_put_ack"}


def dir_table(enable_e: bool) -> dict:
    t = {
        (DI, GETM):            (DM, {"send_data", "set_owner_req"}),
        (DI, PUTS_NOT_LAST):   (DI, ACK),
        (DI, PUTS_LAST):       (DI, ACK),
        (DI, PUTM_NON_OWNER):  (DI, ACK),
        (DI, PUTE_NON_OWNER):  (DI, ACK),

        (DS, GETS):            (DS, {"send_data", "add_sharer"}),
        (DS, GETM):            (DM, {"send_data", "send_inv_others",
                                     "clear_sharers", "set_owner_req"}),
        (DS, PUTS_NOT_LAST):   (DS, REMOVE_ACK),
        (DS, PUTS_LAST):       (DI, REMOVE_ACK),
        (DS, PUTM_NON_OWNER):  (DS, REMOVE_ACK),
        (DS, PUTE_NON_OWNER):  (DS, REMOVE_ACK),

        (DE, GETS):            (SD, {"send_fwd_gets", "sharers_owner_req",
                                     "clear_owner"}),
        (DE, GETM):            (DM, {"send_fwd_getm", "set_owner_req"}),
        (DE, PUTS_NOT_LAST):   (DE, ACK),
        (DE, PUTS_LAST):       (DE, ACK),
        (DE, PUTM_OWNER):      (DI, {"copy_data_l2", "send_put_ack", "clear_owner"}),
        (DE, PUTM_NON_OWNER):  (DE, ACK),
        (DE, PUTE_OWNER):      (DI, {"send_put_ack", "clear_owner"}),
        (DE, PUTE_NON_OWNER):  (DE, ACK),

        (DM, GETS):            (SD, {"send_fwd_gets", "sharers_owner_req",
                                     "clear_owner"}),
        (DM, GETM):            (DM, {"send_fwd_getm", "set_owner_req"}),
        (DM, PUTS_NOT_LAST):   (DM, ACK),
        (DM, PUTS_LAST):       (DM, ACK),
        (DM, PUTM_OWNER):      (DI, {"copy_data_l2", "send_put_ack", "clear_owner"}),
        (DM, PUTM_NON_OWNER):  (DM, ACK),
        (DM, PUTE_OWNER):      (DM, ACK),
        (DM, PUTE_NON_OWNER):  (DM, ACK),

        (SD, GETS):            (SD, {"stall"}),
        (SD, GETM):            (SD, {"stall"}),
        (SD, PUTS_NOT_LAST):   (SD, REMOVE_ACK),
        (SD, PUTS_LAST):       (SD, REMOVE_ACK),
        (SD, PUTM_OWNER):      (SD, REMOVE_ACK),
        (SD, PUTM_NON_OWNER):  (SD, REMOVE_ACK),
        (SD, PUTE_OWNER):      (SD, REMOVE_ACK),
        (SD, PUTE_NON_OWNER):  (SD, REMOVE_ACK),
        (SD, DATA):            (DS, {"copy_data_l2"}),
    }
    # The single arc that E changes.
    if enable_e:
        t[(DI, GETS)] = (DE, {"send_data_e", "set_owner_req"})
    else:
        t[(DI, GETS)] = (DS, {"send_data", "add_sharer"})
    return t
