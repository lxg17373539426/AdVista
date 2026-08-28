import unittest

from ad_vista_agent.runtime.stage_cache import stage_cache_key


class StageCacheTests(unittest.TestCase):
    def test_key_is_stable_across_dictionary_order(self) -> None:
        self.assertEqual(stage_cache_key({"a": 1, "b": 2}), stage_cache_key({"b": 2, "a": 1}))

    def test_configuration_change_changes_key(self) -> None:
        self.assertNotEqual(stage_cache_key({"threshold": 27}), stage_cache_key({"threshold": 30}))


if __name__ == "__main__":
    unittest.main()
