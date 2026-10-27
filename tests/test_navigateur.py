import os
import unittest
from unittest import mock

from darkcodex import navigateur


class NavigateurTests(unittest.TestCase):
    def test_refuse_les_urls_dangereuses(self):
        for url in ("--help", "file:///etc/passwd", "javascript:alert(1)", "https://a b", "", None):
            self.assertFalse(navigateur.url_sure(url), url)
        self.assertTrue(navigateur.url_sure("https://darkcodex.onrender.com/device?code=DARK-AAAA-BBBB"))

    def test_desactivation_par_variable(self):
        with mock.patch.dict(os.environ, {"DARKCODEX_NO_BROWSER": "1"}), mock.patch.object(
            navigateur.webbrowser, "open"
        ) as ouvrir:
            self.assertFalse(navigateur.ouvrir("https://x.test"))
            ouvrir.assert_not_called()

    def test_termux_open_url_en_premier(self):
        appels = []
        with mock.patch.dict(os.environ, {"TERMUX_VERSION": "0.118"}, clear=False), mock.patch.object(
            navigateur.shutil, "which", side_effect=lambda c: "/bin/" + c
        ), mock.patch.object(
            navigateur.subprocess,
            "run",
            side_effect=lambda cmd, **k: appels.append(cmd) or mock.Mock(returncode=0),
        ):
            self.assertTrue(navigateur.ouvrir("https://x.test"))

        self.assertEqual(appels[0], ["termux-open-url", "https://x.test"])

    def test_repli_sur_webbrowser_puis_echec_silencieux(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            navigateur.shutil, "which", return_value=None
        ), mock.patch.object(navigateur.webbrowser, "open", return_value=True):
            self.assertTrue(navigateur.ouvrir("https://x.test"))

        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            navigateur.shutil, "which", return_value=None
        ), mock.patch.object(navigateur.webbrowser, "open", side_effect=RuntimeError("boom")):
            self.assertFalse(navigateur.ouvrir("https://x.test"))


if __name__ == "__main__":
    unittest.main()
