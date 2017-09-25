#!/home/adam/virtualenvs/escrow/bin/python
from __future__ import print_function
import jmbitcoin as btc
import pytest
import sys
import os
data_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(data_dir))

from coinswap import cs_single, get_log, load_coinswap_config
from jmclient import sync_wallet
from bad_participants import (BadAlice, BadCarol,
                              AliceBadHandshake, AliceWrongSecret,
                              AliceBadNegotiate, AliceBadCompleteNegotiation,
                              AliceFailSendTX0id, AliceFailReceiveTX1id,
                              AliceBadTX3Sig, AliceNoBrTX0, AliceBadTX01Monitor,
                              AliceFailReceiveTX5Sig,
                              CarolBadHandshake, CarolBadNegotiate,
                              CarolFailSendTX1id, CarolFailReceiveTX3Sig,
                              CarolNoBrTX1, CarolFailReceiveSecret,
                              CarolBadSendTX5Sig, CarolFailReceiveTX4Sig)

from twisted.internet import reactor, task
from twisted.python import log

from commontest import make_wallets, make_sign_and_push
from coinswap_run import main_cs

cslog = get_log()

alice_classes = {"cooperative": None,
                 "fakesecret": AliceWrongSecret,
                 "badhandshake": AliceBadHandshake,
                 "badncs": AliceBadNegotiate,
                 "badcompleten": AliceBadCompleteNegotiation,
                 "badsendtx0id": AliceFailSendTX0id,
                 "badreceivetx1id": AliceFailReceiveTX1id,
                 "badsendtx3sig": AliceBadTX3Sig,
                 "nobroadcasttx0": AliceNoBrTX0,
                 "notx01monitor": AliceBadTX01Monitor,
                 "badreceivetx5sig": AliceFailReceiveTX5Sig,
                 "rfakesecret": BadAlice}

carol_classes = {"cbadhandshake": CarolBadHandshake,
                 "cbadnegotiate": CarolBadNegotiate,
                 "cbadsendtx1id": CarolFailSendTX1id,
                 "cbadreceivetx3sig": CarolFailReceiveTX3Sig,
                 "cnobroadcasttx1": CarolNoBrTX1,
                 "cbadreceivesecret": CarolFailReceiveSecret,
                 "cbadsendtx5sig": CarolBadSendTX5Sig,
                 "cbadreceivetx4sig": CarolFailReceiveTX4Sig}

alice_funds_not_moved_cases = ["badhandshake", "badncs", "badcompleten",
                               "badsendtx0id", "badreceivetx1id",
                               "badsendtx3sig", "nobroadcasttx0",
                               "cbadreceivetx3sig",
                               "cbadhandshake", "cbadnegotiate", "cbadsendtx1id"]

alice_recover_cases = {}
for i in range(3, 12):
    rt = "ra" + str(i)
    alice_recover_cases[rt] = i
    alice_classes[rt] = BadAlice
    if i < 7:
        alice_funds_not_moved_cases.append(rt)

carol_funds_not_moved_cases = ["cnobroadcasttx1"]
carol_recover_cases = {}
for i in range(3, 10):
    rt = "rc" + str(i)
    carol_recover_cases[rt] = i
    carol_classes[rt] = BadCarol
    if i < 5:
        alice_funds_not_moved_cases.append(rt)
    if i < 6:
        carol_funds_not_moved_cases.append(rt)

carol_funds_not_moved_cases += alice_funds_not_moved_cases

"""parametrize is not allowed with injected config vars from command line;
also, multiple runs of the reactor is not supported. So, we just statically
set the necessary parameters.
"""
num_alices = 1
wallet_structures = [[1, 0, 0]]*2
amounts = [10000000]
funding_amount = 2.0
dest_addr = None
fixed_seeds = False

#fees are low on regtest; tests will fail if larger than this
reasonable_fee_maximum = 20000

class Options(object):
    recover = False
    serverport = "http://127.0.0.1:7080"
    fastsync= False
    serve = True

def miner():
    cs_single().bc_interface.tick_forward_chain(1)

def start_mining(l):
    l.start(4.0)

def runcase(alice_class, carol_class, fail_alice_state=None, fail_carol_state=None):
    options_server = Options()
    wallets = make_wallets(num_alices + 1,
                               wallet_structures=wallet_structures,
                               mean_amt=funding_amount)
    args_server = ["dummy"]
    test_data_server = (wallets[num_alices]['seed'], args_server, options_server,
                        False, None, carol_class, None, fail_carol_state)
    carol_bbmb = main_cs(test_data_server)
    options_alice = Options()
    options_alice.serve = False
    alices = []
    for i in range(num_alices):
        args_alice = ["dummy", amounts[i]]
        if dest_addr:
            args_alice.append(dest_addr)
        test_data_alice = (wallets[i]['seed'], args_alice, options_alice, False,
                           alice_class, None, fail_alice_state, None)
        alices.append(main_cs(test_data_alice))
    l = task.LoopingCall(miner)
    reactor.callWhenRunning(start_mining, l)
    reactor.run()
    return (alices, carol_bbmb, wallets[num_alices]['wallet'])

def test_run_both(setup_wallets, runtype):
    #hack to account for the fact that Carol does not even run
    #if the handshake is bad; this is done to force the reactor to stop.
    if runtype == "badhandshake":
        cs_single().num_entities_running = 1
    #The setup of each test case is the same; the only difference is the
    #participant classes (only Alice for now)
    ac = alice_classes[runtype] if runtype in alice_classes else None
    cc = carol_classes[runtype] if runtype in carol_classes else None
    fail_alice_state = alice_recover_cases[
        runtype] if runtype in alice_recover_cases else None
    fail_carol_state = carol_recover_cases[
        runtype] if runtype in carol_recover_cases else None
    alices, carol_bbmb, carol_wallet = runcase(ac, cc, fail_alice_state,
                                               fail_carol_state)
    #test case function will only return on reactor shutdown; Alice and Carol
    #objects are set at the start, but are references so updated.
    #Check the wallet states reflect the expected updates.
    #TODO handle multiple alices with different amounts against one Carol.
    if runtype == "badhandshake":
        for a in alices:
            a.bbma = a.wallet.get_balance_by_mixdepth(verbose=False)

    expected_spent = reasonable_fee_maximum*4 + cs_single(
        ).config.getint("SERVER", "minimum_coinswap_fee")
    if runtype in alice_funds_not_moved_cases:
        for i, alice in enumerate(alices):
            assert alice.bbmb[0] == alice.bbma[0]
    elif runtype in ["cooperative", "cbadreceivetx4sig", "ra11", "rc8", "rc9"]:
        #in all of these cases Alice's payment is complete
        for i, alice in enumerate(alices):
            funds_spent = alice.bbmb[0] - alice.bbma[0]
            funds_received = alice.bbma[1] - alice.bbmb[1]
            assert funds_spent - funds_received <= expected_spent + reasonable_fee_maximum
    else:
        #Ensure Alice did not pay too much and only spent back to 0 depth
        for i, alice in enumerate(alices):
            assert alice.bbma[1] == 0
            funds_spent = alice.bbmb[0] - alice.bbma[0]
            assert funds_spent <= expected_spent

    #Carol is handled a bit differently, since Carol instances are initiated on
    #the fly, we instead query the wallet object directly for the final balances.
    sync_wallet(carol_wallet)
    carol_bbma = carol_wallet.get_balance_by_mixdepth(verbose=False)
    if runtype in carol_funds_not_moved_cases:
        assert carol_bbma[0] >= carol_bbmb[0]
        assert carol_bbma[0] - carol_bbmb[0] <= reasonable_fee_maximum + cs_single(
            ).config.getint("SERVER", "minimum_coinswap_fee")
    elif runtype in ["cooperative", "rc9"]:
        funds_spent = carol_bbmb[0] - carol_bbma[0]
        funds_received = carol_bbma[1] - carol_bbmb[1]
        assert funds_received - funds_spent >= cs_single(
            ).config.getint("SERVER", "minimum_coinswap_fee") - reasonable_fee_maximum
    else:
        #All cases of backout and funds have moved
        assert carol_bbmb[1] == 0
        #Here we assert carol did not lose money; the alice checks are sufficient
        #to ensure carol didn't get too much
        assert carol_bbma[0] - carol_bbmb[0] > 0

@pytest.fixture(scope="module")
def setup_wallets():
    log.startLogging(sys.stdout)    
    load_coinswap_config()
    #need to give up waiting for confirms artificially quickly
    cs_single().one_confirm_timeout = 20
    cs_single().num_entities_running = 0
