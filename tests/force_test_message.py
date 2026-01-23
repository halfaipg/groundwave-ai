#!/usr/bin/env python3
"""Force test by checking if Meshtastic is receiving ANY packets."""

import sys
sys.path.insert(0, '/Users/j/meshbot')

import time

try:
    import meshtastic.serial_interface
    from pubsub import pub
    
    print("=" * 70)
    print("🔍 Direct Meshtastic Receive Test")
    print("=" * 70)
    print()
    print("Connecting to Meshtastic device...")
    
    received_count = 0
    
    def on_receive(packet, interface):
        global received_count
        received_count += 1
        print(f"\n📨 PACKET #{received_count} RECEIVED!")
        print(f"   From: {packet.get('fromId', 'unknown')}")
        print(f"   To: {packet.get('toId', 'unknown')}")
        if 'decoded' in packet:
            if 'text' in packet.get('decoded', {}):
                print(f"   Text: {packet['decoded']['text']}")
            print(f"   Type: {packet['decoded'].get('portnum', 'unknown')}")
        print()
    
    def on_connection(interface, topic=None):
        print("✅ Connected to Meshtastic!")
        print(f"   My Node: {interface.myInfo.my_node_num:08x}")
        print()
        print("👂 Listening for packets... (30 seconds)")
        print("   Send a message from your device now!")
        print()
    
    pub.subscribe(on_receive, "meshtastic.receive")
    pub.subscribe(on_connection, "meshtastic.connection.established")
    
    interface = meshtastic.serial_interface.SerialInterface('/dev/cu.usbmodem80B54EF06B541')
    
    # Wait 30 seconds
    for i in range(30):
        time.sleep(1)
        if i % 5 == 0 and i > 0:
            print(f"   [{i}/30] Received {received_count} packets so far...")
    
    print()
    print("=" * 70)
    print(f"RESULT: Received {received_count} total packets in 30 seconds")
    print("=" * 70)
    
    if received_count == 0:
        print()
        print("⚠️  NO PACKETS RECEIVED!")
        print()
        print("Possible issues:")
        print("  1. No one sent a message during the test")
        print("  2. Meshtastic device not receiving (check antenna/position)")
        print("  3. Device in wrong channel/frequency")
        print()
    else:
        print()
        print("✅ Device IS receiving packets!")
        print("   If bot didn't respond, there's a software issue.")
    
    interface.close()
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
