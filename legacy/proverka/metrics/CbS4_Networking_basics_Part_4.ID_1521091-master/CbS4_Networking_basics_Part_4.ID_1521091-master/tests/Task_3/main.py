from scapy.all import IP, send, TCP

if __name__ == "__main__":
    message = "Dear Steel Cat! This is no attack, it's my humster Pinkie you should track"
    packet = IP(dst="127.0.0.1") / TCP(dport=12345) / message
    send(packet)

