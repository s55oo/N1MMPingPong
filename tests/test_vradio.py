"""N1MM_vRadio2 tells PingPong which PC really keys the shared radio."""
import unittest

from helpers import MY_IP, m, make_lamp, peer, station

HOST, CLIENT = "10.147.17.116", "10.147.17.187"


def share(tx="none", frm="", host=HOST, clients=CLIENT, seq=1, ident="L15CAB-1"):
    return ("<vRadioShare><Id>{}</Id><Host>L15CAB</Host><Version>1.0.9</Version>"
            "<Ips>{}</Ips><Radio>FTDX101D</Radio><HostVfo>A</HostVfo><ClientVfo>B</ClientVfo>"
            "<Clients>{}</Clients><Tx>{}</Tx><TxFrom>{}</TxFrom><Seq>{}</Seq></vRadioShare>"
            ).format(ident, host, clients, tx, frm, seq).encode()


def radio(tx, freq="352310", caption=""):
    cap = "<FunctionKeyCaption>{}</FunctionKeyCaption>".format(caption) if caption else ""
    return ("<RadioInfo><RadioNr>1</RadioNr><Freq>{}</Freq><TXFreq>{}</TXFreq><Mode>CW</Mode>"
            "<IsTransmitting>{}</IsTransmitting>{}</RadioInfo>").format(
                freq, freq, "True" if tx else "False", cap).encode()


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.v = m.VRadioShare()

    def test_not_in_a_share_is_none(self):
        self.v.feed(share("B", CLIENT), 100)
        self.assertIsNone(self.v.verdict({"10.9.9.9"}, 100))

    def test_client_keys(self):
        self.v.feed(share("B", CLIENT), 100)
        self.assertTrue(self.v.verdict({CLIENT}, 100))
        self.assertFalse(self.v.verdict({HOST}, 100))

    def test_host_keys_local_dj_or_host_vfo(self):
        for frm in ("local", "dj", ""):
            v = m.VRadioShare()
            v.feed(share("A", frm), 100)
            self.assertTrue(v.verdict({HOST, "192.168.0.2"}, 100), frm)
            self.assertFalse(v.verdict({CLIENT}, 100), frm)

    def test_nobody(self):
        self.v.feed(share("none"), 100)
        self.assertFalse(self.v.verdict({HOST}, 100))
        self.assertFalse(self.v.verdict({CLIENT}, 100))

    def test_stale_share_is_dropped(self):
        self.v.feed(share("B", CLIENT), 100)
        self.assertIsNone(self.v.verdict({HOST}, 103.5))
        self.assertTrue(self.v.expire(103.5))
        self.assertFalse(self.v.expire(104))

    def test_feed_reports_changes_only(self):
        self.assertTrue(self.v.feed(share("B", CLIENT, seq=1), 100))
        self.assertFalse(self.v.feed(share("B", CLIENT, seq=2), 101))
        self.assertTrue(self.v.feed(share("none", seq=3), 102))
        self.assertFalse(self.v.feed(b"<RadioInfo/>", 102))


class GuardTest(unittest.TestCase):
    def test_host_echo_ignored_while_client_keys(self):
        g = m.SharedTx()
        g.vradio.feed(share("B", CLIENT), 100)
        host, client = m.RadioSet(), m.RadioSet()
        host.update(radio(True), 100.0, guard=g, pc="H", ips={HOST})
        client.update(radio(True, caption="F1 CQ"), 100.02, guard=g, pc="C", ips={CLIENT})
        self.assertFalse(host.select(100.1).tx)
        self.assertTrue(client.select(100.1).tx)

    def test_other_freq_vfos_are_still_one_radio(self):
        # host on VFO-A 3.5277, client on VFO-B 3.5231: different frequencies
        g = m.SharedTx()
        g.vradio.feed(share("B", CLIENT), 100)
        host, client = m.RadioSet(), m.RadioSet()
        host.update(radio(True, freq="352777"), 100.0, guard=g, pc="H", ips={HOST})
        client.update(radio(True, freq="352310", caption="CQ"), 100.0, guard=g, pc="C", ips={CLIENT})
        self.assertFalse(host.select(100.1).tx)

    def test_unpaired_pc_unchanged(self):
        g = m.SharedTx()
        g.vradio.feed(share("B", CLIENT), 100)
        rs = m.RadioSet()
        rs.update(radio(True), 100.0, guard=g, pc="X", ips={"10.9.9.9"})
        self.assertTrue(rs.select(100.1).tx)


class LampTest(unittest.TestCase):
    def test_tx_follows_vradio_even_if_the_share_packet_comes_last(self):
        app = make_lamp(stale=15.0)
        app.local, app.local_nets = {MY_IP, "127.0.0.1", HOST}, {MY_IP, HOST}
        app.on_packet(MY_IP, radio(True), relayed=False)  # my own N1MM says TX (echo)
        mine = app._local_station()
        self.assertTrue(mine.tx)
        app.on_vradio(share("B", CLIENT))                 # vRadio: the client keys
        self.assertFalse(app._local_station().tx)
        app.on_vradio(share("none", seq=2))
        self.assertFalse(app._local_station().tx)

    def test_diagnostics_log_shows_verdict_and_addresses(self):
        app = make_lamp(stale=15.0, test_on=True)
        lines = []
        app._test_write = lines.append
        app.local, app.local_nets = {MY_IP, "127.0.0.1", HOST}, {MY_IP, HOST}
        app.on_packet(MY_IP, radio(False), relayed=False)
        app.on_vradio(share("B", CLIENT))
        text = "\n".join(lines)
        self.assertIn("vRadioShare  Tx=B  from=" + CLIENT, text)
        self.assertIn("=False", text)
        self.assertIn("this PC's addresses", text)

    def test_without_a_share_nothing_changes(self):
        app = make_lamp(stale=15.0)
        app.on_packet(MY_IP, radio(True), relayed=False)
        self.assertTrue(app._local_station().tx)


if __name__ == "__main__":
    unittest.main()
