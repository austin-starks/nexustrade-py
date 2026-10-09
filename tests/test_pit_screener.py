import unittest
import nexustrade as nt


class PitScreenerTests(unittest.TestCase):
    def test_saved_screen_with_native_sources(self):
        manager = nt.update_institutional_watchlist(watchlist_key="manager", manager_cik="1067983")
        insider = nt.update_insider_watchlist(watchlist_key="insider", window_days=45, role="Officer")
        screen = nt.update_screener_watchlist(watchlist_key="screen", refresh_minutes=1440,
            columns=[nt.screen_column("manager", nt.screen_disclosure(manager["source"])),
                     nt.screen_column("insider", nt.screen_disclosure(insider["source"])),
                     nt.screen_column("price", nt.screen_price())],
            filter=nt.screen_all(nt.screen_compare("price", "Gte", 5), nt.screen_compare("insider", "Gt", 0)),
            selection=nt.screen_top("manager", 20))
        self.assertEqual(screen["source"]["type"], "Screener")
        self.assertEqual(screen["source"]["refreshMinutes"], 1440)
        self.assertEqual(screen["source"]["selection"]["direction"], "Descending")
        self.assertNotIn("output", screen)
        self.assertNotIn("windowDays", manager["source"])


if __name__ == "__main__":
    unittest.main()
