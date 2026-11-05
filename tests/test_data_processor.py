import unittest
from app.data_processor import process_user_metrics


class TestDataProcessor(unittest.TestCase):
    def test_metrics_calculation(self):
        result = process_user_metrics([10, 20, 30])
        self.assertEqual(result["total"], 60)
        self.assertEqual(result["average"], 20.0)

    def test_empty_metrics(self):
        result = process_user_metrics([])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["average"], 0.0)


if __name__ == "__main__":
    unittest.main()
