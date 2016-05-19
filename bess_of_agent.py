#!/usr/bin/python
import twink
from twink.ofp4 import *
import twink.ofp4.build as b
import twink.ofp4.parse as p
import twink.ofp4.oxm as oxm
import threading
import binascii
import logging
import signal
import errno
import time
import sys
import os
from collections import namedtuple
logging.basicConfig(level=logging.ERROR)

PHY_NAME = "dpif"

ofp_port_names = '''port_no hw_addr name config state
                    curr advertised supported peer curr_speed max_speed'''
of_port = namedtuple('of_port', ofp_port_names)
default_port = of_port('<port no>', '<mac address>', '<port name>', 0, 0,
                       0x802, 0, 0, 0, 0, 0)
of_ports = {
    OFPP_LOCAL: default_port._replace(port_no=OFPP_LOCAL, hw_addr=binascii.a2b_hex("0000deadbeef"), name='br-int', curr=0),
    1: default_port._replace(port_no=1, hw_addr=binascii.a2b_hex("0000deaddead"), name='vxlan', curr=0),
    2: default_port._replace(port_no=2, hw_addr=binascii.a2b_hex("000000000001"), name=PHY_NAME, curr=0),
}

flows = {}
channel = 0

try:
    BESS_PATH = os.getenv('BESSDK','/opt/bess')
    sys.path.insert(1, '%s/libbess-python' % BESS_PATH)
    from bess import *
except ImportError as e:
    print >> sys.stderr, 'Cannot import the API module (libbess-python)', e.message
    sys.exit()


def connect_bess():

    s = BESS()
    try:
        s.connect()
    except s.APIError as e:
        print >> sys.stderr, e.message
        sys.exit()
    else:
        return s


def init_phy_port(bess, name, port_id):

    try:
        result = bess.create_port('PMD', name, {'port_id': port_id })
    except (bess.APIError, bess.Error)as err:
        print err.message
        return {'name': None}
    else:
        return result


def switch_proc(message, ofchannel):

    msg = p.parse(message)

    # TODO: Acquire lock

    if msg.header.type == OFPT_FEATURES_REQUEST:
        channel.send(b.ofp_switch_features(b.ofp_header(4, OFPT_FEATURES_REPLY, 0, msg.header.xid), 1, 2, 3, 0, 0xF))

    elif msg.header.type == OFPT_GET_CONFIG_REQUEST:
        channel.send(b.ofp_switch_config(b.ofp_header(4, OFPT_GET_CONFIG_REPLY, 0, msg.header.xid), 0, 0xffe5))

    elif msg.header.type == OFPT_ROLE_REQUEST:
        channel.send(b.ofp_role_request(b.ofp_header(4, OFPT_ROLE_REPLY, 0, msg.header.xid), msg.role, msg.generation_id))

    elif msg.header.type == OFPT_FLOW_MOD:
        if msg.cookie not in flows:
            flows[msg.cookie] = msg
        else:
            print "I already have this FlowMod: Cookie", \
                msg.cookie, oxm.parse_list(flows[msg.cookie].match), (flows[msg.cookie].instructions)

    elif msg.header.type == OFPT_MULTIPART_REQUEST:
        if msg.type == OFPMP_FLOW:
            channel.send(b.ofp_multipart_reply(b.ofp_header(4, OFPT_MULTIPART_REPLY, 0, msg.header.xid),
                         msg.type, 0, ["".join(b.ofp_flow_stats(None, f.table_id, 1, 2, f.priority,
                                                                f.idle_timeout, f.hard_timeout, f.flags, f.cookie, 0, 0,
                                                                f.match, f.instructions)
                                               for f in flows.itervalues())]))
        elif msg.type == OFPMP_PORT_DESC:
            channel.send(b.ofp_multipart_reply(b.ofp_header(4, OFPT_MULTIPART_REPLY, 0, msg.header.xid),
                         msg.type, 0, ["".join(b.ofp_port(ofp.port_no, ofp.hw_addr, ofp.name, ofp.config, ofp.state,
                                                          ofp.curr, ofp.advertised, ofp.supported, ofp.peer, ofp.curr_speed, ofp.max_speed)
                                               for ofp in of_ports.itervalues())]))
        elif msg.type == OFPMP_DESC:
            channel.send(b.ofp_multipart_reply(b.ofp_header(4, OFPT_MULTIPART_REPLY, 0, msg.header.xid),
                         msg.type, 0, 
                         b.ofp_desc("UC Berkeley", "Intel Xeon", "BESS", "commit-6e343", None)))

    elif msg.header.type ==  OFPT_PACKET_OUT:
        print msg

    elif msg.header.type == OFPT_HELLO:
        pass

    elif msg.header.type == OFPT_SET_CONFIG:
        pass

    elif msg.header.type == OFPT_BARRIER_REQUEST:
        pass

    else:
        print msg
        assert 0

    # TODO: Release lock


def of_agent_start(ctl_ip='127.0.0.1', port=6653):

    global channel
    socket = twink.sched.socket
    try:
        s = socket.create_connection((ctl_ip, port),)
    except socket.error as err:
        if err.errno != errno.ECONNREFUSED:
            raise err
        print 'Is the controller running at %s:%d' % (ctl_ip, port)
        return errno.ECONNREFUSED

    ch = type("Switch", (
        twink.AutoEchoChannel,
        twink.LoggingChannel,), {
                  "accept_versions": [4, ],
                  "handle": staticmethod(switch_proc)
              })()
    ch.attach(s)
    channel = ch

    t1 = threading.Thread(name="Switch Loop", target=ch.loop)
    t1.setDaemon(True)
    t1.start()


def print_stupid():
    while 1:
        print "#######################  Stupid  #######################"
        time.sleep(2)
    pass


if __name__ == "__main__":

    dp = connect_bess()

    def cleanup(*args):
        dp.destroy_port(PHY_NAME)
        print "Deleted PMD port %s" % PHY_NAME
        sys.exit()

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    if init_phy_port(dp, PHY_NAME, 0)['name'] == PHY_NAME:
        print "Successfully created PMD port : %s" % PHY_NAME
    else:
        print 'Failed to create PMD port. Check if it exists already'

    print 'Initial list of Openflow ports', of_ports

    while of_agent_start() == errno.ECONNREFUSED:
        pass


    # TODO: Connect to BESS and create PACKET_[IN,OUT] ports using UNIX socket for all non-LOCAL ports
    # TODO: Start a thread that will select poll on all of those UNIX sockets
    t2 = threading.Thread(name="Stupid Thread", target=print_stupid)
    t2.setDaemon(True)
    t2.start()

    signal.pause()
