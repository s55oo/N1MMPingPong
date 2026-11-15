"""Echo of a shared radio: a PC's N1MM may report TX without a caption right
after the other PC's captioned TX. Real packets (S51CAB, 15 Nov 2026)."""
import unittest

import pingpong_common as m

# (time, PC, caption, tx, freq) - RUN2 = L (host), RUN3 = S; all CW, same band.
REAL = [
    (45.026, "L", "F4 S51CAB", True, 352777), (45.588, "L", "", False, 352777),
    (45.934, "S", "F4 S51CAB", True, 352310), (46.015, "S", "F4 S51CAB", True, 352310),
    (46.087, "L", "", True, 352777),   # echo: RUN2's N1MM reports TX right after RUN3
    (47.192, "L", "", False, 352777), (47.574, "S", "F4 S51CAB", True, 352310),
    (48.475, "S", "", False, 352310),
    (50.335, "L", "F4 S51CAB", True, 352777), (50.908, "L", "", False, 352777),
    (51.373, "S", "F4 S51CAB", True, 352310), (51.587, "L", "", True, 352777),  # echo
    (52.130, "L", "", False, 352777), (52.078, "S", "", False, 352310),
]


def xml(caption, tx, freq):
    cap = "<FunctionKeyCaption>{}</FunctionKeyCaption>".format(caption) if caption else ""
    return ("<RadioInfo><RadioNr>1</RadioNr><Freq>{0}</Freq><TXFreq>{0}</TXFreq><Mode>CW</Mode>"
            "<IsTransmitting>{1}</IsTransmitting>{2}</RadioInfo>").format(
                freq, "True" if tx else "False", cap).encode()


class Net:
    def __init__(self, use_filter=True):
        self.g = m.EchoFilter() if use_filter else None
        self.sets = {"L": m.RadioSet(), "S": m.RadioSet()}
        self.events = []

    def send(self, who, caption, tx, freq, now):
        ev = self.sets[who].update(xml(caption, tx, freq), now, guard=self.g, pc=who)
        if ev and ev[1]:
            self.events.append((who, ev[0].msg))
        return ev

    def tx(self, who, now):
        r = self.sets[who].select(now)
        return bool(r and r.tx)


class EchoTest(unittest.TestCase):
    def test_real_packets_no_transmission_without_message(self):
        n = Net()
        for ts, who, cap, tx, f in REAL:
            n.send(who, cap, tx, f, ts)
        self.assertEqual([e for e in n.events if not e[1]], [])
        self.assertEqual([w for w, _ in n.events], ["L", "S", "L", "S"])

    def test_without_the_filter_the_echo_shows_without_message(self):
        n = Net(use_filter=False)
        for ts, who, cap, tx, f in REAL:
            n.send(who, cap, tx, f, ts)
        self.assertEqual(len([e for e in n.events if not e[1]]), 2)

    def test_echo_is_ignored_while_the_other_pc_transmits(self):
        n = Net()
        n.send("S", "F4 S51CAB", True, 352310, 100.0)
        n.send("L", "", True, 352777, 100.1)
        self.assertTrue(n.tx("S", 100.2))
        self.assertFalse(n.tx("L", 100.2))

    def test_long_message_without_new_packets_still_filters_the_echo(self):
        n = Net()
        n.send("S", "CQ TEST S51CAB", True, 352310, 100.0)
        n.send("L", "", True, 352777, 104.0)  # 4 s later, RUN3 still transmitting
        self.assertFalse(n.tx("L", 104.1))

    def test_own_message_continues_without_caption(self):
        n = Net()
        n.send("L", "F4 S51CAB", True, 352777, 100.0)
        n.send("L", "", False, 352777, 100.4)
        n.send("L", "", True, 352777, 100.9)
        self.assertTrue(n.tx("L", 101.0))
        self.assertEqual(n.events, [("L", "S51CAB"), ("L", "S51CAB")])

    def test_lone_transmission_without_caption_is_kept(self):
        n = Net()
        n.send("L", "", True, 352777, 100.0)
        self.assertTrue(n.tx("L", 100.1))

    def test_other_band_is_not_an_echo(self):
        n = Net()
        n.send("S", "CQ", True, 701500, 100.0)
        n.send("L", "", True, 352777, 100.1)
        self.assertTrue(n.tx("L", 100.2))

    def test_old_caption_of_the_other_pc_is_not_an_echo_later(self):
        n = Net()
        n.send("S", "F4", True, 352310, 100.0)
        n.send("S", "", False, 352310, 100.5)
        n.send("L", "", True, 352777, 103.0)  # 3 s later: RUN2 transmits on its own
        self.assertTrue(n.tx("L", 103.1))


if __name__ == "__main__":
    unittest.main()


class VerdictTests(unittest.TestCase):
    """The diagnostics log shows N1MM's raw values next to PingPong's verdict."""

    def test_echo_verdict(self):
        n = Net()
        n.send("S", "F4 S51CAB", True, 352310, 100.0)
        n.send("L", "", True, 352777, 100.2)
        v = n.sets["L"].verdict
        self.assertTrue(v["n1mm_tx"])
        self.assertFalse(v["tx"])
        self.assertIn("ECHO", v["note"])

    def test_caption_carried_verdict(self):
        n = Net()
        n.send("L", "F4 S51CAB", True, 352777, 100.0)
        n.send("L", "", True, 352777, 100.4)
        v = n.sets["L"].verdict
        self.assertTrue(v["tx"])
        self.assertEqual(v["key"], "S51CAB")
        self.assertIn("carried over", v["note"])

    def test_plain_packet_has_no_note(self):
        n = Net()
        n.send("L", "", False, 352777, 100.0)
        self.assertEqual(n.sets["L"].verdict["note"], "")


class TailTest(unittest.TestCase):
    """Real log, 15 Nov 12:28:07: RUN2's TX 0.58 s after RUN3's TX ended is not an echo."""

    def test_tx_well_after_the_other_ended_is_kept(self):
        n = Net()
        n.send("S", "F4 S51CAB", True, 352310, 100.0)
        n.send("S", "", False, 352310, 100.5)
        n.send("L", "", True, 352777, 101.08)  # 0.58 s after RUN3 ended
        self.assertTrue(n.tx("L", 101.1))

    def test_echo_right_after_the_other_ended_is_ignored(self):
        n = Net()
        n.send("S", "F4 S51CAB", True, 352310, 100.0)
        n.send("S", "", False, 352310, 100.5)
        n.send("L", "", True, 352777, 100.7)  # 0.2 s after: relay lag
        self.assertFalse(n.tx("L", 100.8))
