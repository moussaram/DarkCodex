    def test_device_page_configuration_priorities(self):
        with mock.patch.dict("os.environ", {"DARKCODEX_PUBLIC_URL": "https://custom.example/", "RENDER_EXTERNAL_URL": "https://render.example"}, clear=False):
            self.assertEqual(srv_device._url_publique(), "https://custom.example")
        with mock.patch.dict("os.environ", {"DARKCODEX_PUBLIC_URL": "", "RENDER_EXTERNAL_URL": "https://render.example/"}, clear=False):
            self.assertEqual(srv_device._url_publique(), "https://render.example")
        with mock.patch.dict("os.environ", {"DARKCODEX_PUBLIC_URL": "", "RENDER_EXTERNAL_URL": ""}, clear=False):
            self.assertEqual(srv_device._url_publique(), "https://darkcodex-technicien.onrender.com")
        with mock.patch.dict("os.environ", {"DARKCODEX_PUBLIC_URL": "https://custom.example", "DARKCODEX_DEVICE_URL": ""}, clear=False):
            self.assertEqual(srv_device._url_page_device(), "https://custom.example/device")
        with mock.patch.dict("os.environ", {"DARKCODEX_DEVICE_URL": "https://login.example/connect"}, clear=False):
            self.assertEqual(srv_device._url_page_device(), "https://login.example/connect")

    def test_import_serveur_et_route_de_liaison_sont_coherents(self):
        self.assertIs(srv_main.device, srv_device)
        self.assertEqual(srv_main.DEVICE_PAGE, srv_device.DEVICE_PAGE_URL)
        self.assertIn("/device", srv_main.PAGES)
        with mock.patch.object(srv_device, "nettoyer_codes_expires"), mock.patch.object(
            srv_device, "creer_code_unique", return_value="DARK-4829"
        ):
            status, data = call(srv_main.device_code)
        self.assertEqual(status, 200)
        self.assertEqual(data["lien"], srv_main.DEVICE_PAGE)
        self.assertTrue(data["lien"].startswith(("https://", "http://")))

