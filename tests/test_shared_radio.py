"""Two PCs share one radio: only the PC that keys counts as transmitting."""
import unittest

import pingpong_common as m


def pkt(tx, freq="352310", caption="", nr=1):
    cap = "<FunctionKeyCaption>{}</FunctionKeyCaption>".format(caption) if caption else ""
    return ("<RadioInfo><RadioNr>{}</RadioNr><Freq>{}</Freq><TXFreq>{}</TXFreq><Mode>CW</Mode>"
            "<IsTransmitting>{}</IsTransmitting>{}</RadioInfo>").format(
                nr, freq, freq, "True" if tx else "False", cap).encode()


class Net:
    """RUN2 and RUN3 as the lamp sees them."""

    def __init__(self):
        self.guard = m.SharedTx()
        self.sets = {"RUN2": m.RadioSet(), "RUN3": m.RadioSet()}

    def send(self, who, data, now):
        self.sets[who].update(data, now, guard=self.guard, pc=who)

    def tx(self, who, now):
        r = self.sets[who].select(now)
        return bool(r and r.tx)


class SharedRadioTest(unittest.TestCase):
    def test_echo_on_other_pc_is_ignored(self):
        n = Net()
        n.send("RUN3", pkt(True, caption="F1 CQ"), 100.0)
        n.send("RUN2", pkt(True), 100.02)  # the shared radio's TX seen by RUN2's N1MM
        self.assertTrue(n.tx("RUN3", 100.1))
        self.assertFalse(n.tx("RUN2", 100.1))

    def test_ignored_through_long_tx_and_tail(self):
        n = Net()
        n.send("RUN3", pkt(True, caption="CQ"), 100.0)
        n.send("RUN2", pkt(True), 100.0)
        n.send("RUN3", pkt(True), 110.0)  # periodic update, still keying
        n.send("RUN2", pkt(True), 110.1)
        self.assertFalse(n.tx("RUN2", 110.2))
        n.send("RUN3", pkt(False), 111.0)  # RUN3 stops
        n.send("RUN2", pkt(True), 112.0)   # RUN2's N1MM still shows the PTT tail
        self.assertFalse(n.tx("RUN2", 112.1))
        self.assertFalse(n.tx("RUN3", 112.1))

    def test_other_pc_may_transmit_after_the_tail(self):
        n = Net()
        n.send("RUN3", pkt(True, caption="CQ"), 100.0)
        n.send("RUN3", pkt(False), 101.0)
        n.send("RUN2", pkt(True, caption="F2 599"), 103.0)  # 2 s later: its own turn
        self.assertTrue(n.tx("RUN2", 103.1))

    def test_message_caption_wins_over_echo_that_came_first(self):
        n = Net()
        n.send("RUN2", pkt(True), 100.0)                  # echo arrives first
        n.send("RUN3", pkt(True, caption="CQ"), 100.05)    # the real one
        n.send("RUN2", pkt(True), 100.5)
        self.assertTrue(n.tx("RUN3", 100.6))
        self.assertFalse(n.tx("RUN2", 100.6))

    def test_different_frequencies_are_independent(self):
        n = Net()
        n.send("RUN3", pkt(True, freq="352310", caption="CQ"), 100.0)
        n.send("RUN2", pkt(True, freq="701500", caption="CQ"), 100.0)
        self.assertTrue(n.tx("RUN3", 100.1))
        self.assertTrue(n.tx("RUN2", 100.1))

    def test_without_guard_nothing_changes(self):
        s = m.RadioSet()
        s.update(pkt(True), 100.0)
        self.assertTrue(s.select(100.1).tx)

    def test_so2r_two_radios_one_pc_not_confused(self):
        g, s = m.SharedTx(), m.RadioSet()
        s.update(pkt(True, freq="352310", nr=1), 100.0, guard=g, pc="RUN3")
        s.update(pkt(False, freq="701500", nr=2), 100.1, guard=g, pc="RUN3")
        self.assertTrue(s.select(100.2).tx)


if __name__ == "__main__":
    unittest.main()
