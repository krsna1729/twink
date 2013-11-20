from __future__ import absolute_import
from . import *
import struct
from collections import namedtuple

class PortMonitorChannel(ControllerChannel):
	def __init__(self, *args, **kwargs):
		super(PortMonitorChannel, self).__init__(*args, **kwargs)
		self._ports = []
		self._ports_init = self.event()
		self._port_monitor_multi = dict()
	
	def recv(self):
		message = super(PortMonitorChannel, self).recv()
		if message:
			ofp_port = "!H6s16sIIIIII" # ofp_port v1.0
			ofp_port_names = '''port_no hw_addr name
				config state
				curr advertised supported peer'''
			if self.version in (2,3,4):
				ofp_port = "!I4x6s2x16sIIIIIIII"
				ofp_port_names = '''port_no hw_addr name
					config state
					curr advertised supported peer
					curr_speed max_speed'''
			elif self.version == 5:
				ofp_port = "!IH2x6s2x6sII"
				ofp_port_names = '''port_no length hw_addr name
					config state'''
		
			(version, oftype, length, xid) = parse_ofp_header(message)
			if xid in self._port_monitor_multi and oftype==19: # MULTIPART_REPLY
				assert self.version in (4,5)
				(mptype, flags) = struct.unpack_from("!HH4x", message, offset=8)
				if mptype==13: # OFPMP_PORT_DESC
					ports = self._port_monitor_multi[xid]
					offset = 16
					while offset < length:
						port = list(struct.unpack_from(ofp_port, message, offset=offset))
						port[2] = port[2].partition('\0')[0]
						ports.append(namedtuple("ofp_port", ofp_port_names)(*port))
						offset += struct.calcsize(ofp_port)
				
					if not flags&1:
						self._ports = ports
						self._ports_init.set()
						del(self._port_monitor_multi[xid])
			elif oftype==6 and self.version != 4: # FEATURES_REPLY
				fmt = "!BBHIQIB3x"
				assert struct.calcsize(fmt) % 8 == 0
				offset = struct.calcsize(fmt+"II")
				ports = []
				while offset < length:
					port = list(struct.unpack_from(ofp_port, message, offset=offset))
					port[2] = port[2].partition('\0')[0]
					ports.append(namedtuple("ofp_port", ofp_port_names)(*port))
					offset += struct.calcsize(ofp_port)
				self._ports = ports
				self._ports_init.set()
			elif oftype==12: # PORT_STATUS
				p = struct.unpack_from("!B7x"+ofp_port[1:], message, offset=8)
				reason = p[0]
				port = list(p[1:])
				port[2] = port[2].partition('\0')[0]
				self.update_port(reason, namedtuple("ofp_port", ofp_port_names)(*port))
		return message
	
	def update_port(self, reason, port):
		ports = self._ports
		hit = [x for x in ports if x[0]==port[0]] # check with port_no(0)
		if reason==0: # ADD
			if self._ports_init.is_set():
				assert not hit
			ports.append(port)
		elif reason==1: # DELETE
			if self._ports_init.is_set():
				assert hit
			if hit:
				assert len(hit) == 1
				ports.remove(hit.pop())
		elif reason==2: # MODIFY
			if self._ports_init.is_set():
				assert hit
			if hit:
				assert len(hit) == 1
				ports.remove(hit.pop())
			ports.append(port)
		else:
			assert False, "unknown reason %d" % reason
		self._ports = ports
	
	@property
	def ports(self):
		if not self._ports_init.is_set():
			if self.version in (4, 5):
				xid = hms_xid()
				self._port_monitor_multi[xid] = []
				self.send(struct.pack("!BBHIHH4x", self.version, 
					18, # MULTIPART_REQUEST (v1.3, v1.4)
					16, # struct.calcsize(fmt)==16
					xid, 
					13, # PORT_DESC
					0, # no REQ_MORE
					))
			else:
				self.send(ofp_header_only(5, version=self.version)) # FEATURES_REQUEST
			self._ports_init.wait()
		return self._ports
	
	def close(self):
		self._ports_init.set() # unlock the event
		super(PortMonitorChannel, self).close()

