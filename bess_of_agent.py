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
import socket
import errno
import time
import sys
import os
from collections import namedtuple
logging.basicConfig(level=logging.ERROR)

PHY_NAME = "dpif"

ofp_port_names = '''port_no hw_addr name config state
                    curr advertised supported peer curr_speed max_speed
                    pkt_inout_socket'''
of_port = namedtuple('of_port', ofp_port_names)
default_port = of_port('<port no>', '<mac address>', '<port name>', 0, 0,
                       0x802, 0, 0, 0, 0, 0,
                       None)
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
        result = bess.create_port('PMD', name, {'port_id': port_id})
        bess.resume_all()
    except (bess.APIError, bess.Error)as err:
        print err.message
        return {'name': None}
    else:
        return result


PKTINOUT_NAME = 'pktinout_%s'
SOCKET_PATH = '/tmp/bess_unix_' + PKTINOUT_NAME

def init_pktinout_port(bess, name):

    # br-int alone or vxlan too?
    if name == 'br-int':
        return None, None

    try:
        result = bess.create_port('UnixSocket', PKTINOUT_NAME % name, {'path': '@' + SOCKET_PATH % name})
        bess.resume_all()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        s.connect('\0' + SOCKET_PATH % name)
    except (bess.APIError, bess.Error, socket.error) as err:
        print err
        return {'name': None}, None
    else:
        # TODO: Handle VxLAN PacketOut if Correct/&Reqd. Create PI connect PI_pktinout_vxlan-->Encap()-->PO_dpif
        if name != 'vxlan':
            bess.create_module('PortInc', 'PI_' + PKTINOUT_NAME % name, {'port': PKTINOUT_NAME % name})
            bess.create_module('PortOut', 'PO_' + name, {'port': name})
            bess.connect_modules('PI_' + PKTINOUT_NAME % name, 'PO_' + name)
        bess.resume_all()
        return result, s


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
        index = msg.actions[0].port
        print "Packet out OF Port %d, Len:%d" % (index, len(msg.data))
        sock = of_ports[index].pkt_inout_socket
        sent = sock.send(msg.data)
        if sent != len(msg.data):
            print "Incomplete Transmission Sent:%d, Len:%d" % (sent, len(msg.data))

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
    dp.resume_all()

    def cleanup(*args):
        dp.pause_all()
        dp.reset_all()
        sys.exit()

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    if init_phy_port(dp, PHY_NAME, 0)['name'] == PHY_NAME:
        print "Successfully created PMD port : %s" % PHY_NAME
    else:
        print 'Failed to create PMD port. Check if it exists already'

    print 'Initial list of Openflow ports', of_ports

    for port_num, port in of_ports.iteritems():
        ret, sock = init_pktinout_port(dp, port.name)
        of_ports[port_num] = of_ports[port_num]._replace(pkt_inout_socket=sock)
        print ret, ' ', of_ports[port_num].pkt_inout_socket

    while of_agent_start() == errno.ECONNREFUSED:
        pass


    # TODO: Connect to BESS and create PACKET_[IN,OUT] ports using UNIX socket for all non-LOCAL ports
    # TODO: Start a thread that will select poll on all of those UNIX sockets
    t2 = threading.Thread(name="Stupid Thread", target=print_stupid)
    t2.setDaemon(True)
    t2.start()

    signal.pause()
