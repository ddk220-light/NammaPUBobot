"""Offline gold migration checks against the current legacy CSV schema."""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('economy', 'scripts/convert-economy.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
GUILD = '706320010368516198'
USER = '100000000000000100'

class EconomyConverterTests(unittest.TestCase):
    def fixture(self, folder, status='void', payment='refund', amount=50):
        data = {
            'gold_balances.csv': [dict(community_id=GUILD, user_id=USER, balance=450+amount)],
            'gold_ledger.csv': [dict(id=1,community_id=GUILD,user_id=USER,amount=500,entry_type='seed',created_at=1,post_id=''),dict(id=2,community_id=GUILD,user_id=USER,amount=-50,entry_type='bet',created_at=2,post_id=1),dict(id=3,community_id=GUILD,user_id=USER,amount=amount,entry_type=payment,created_at=3,post_id=1)],
            'prediction_posts.csv': [dict(id=1,channel_id='123',match_id=4,status=status,winner_idx='0' if status=='resolved' else '',opened_at=2,freezes_at=3,resolved_at=3)],
            'prediction_bets.csv': [dict(post_id=1,user_id=USER,side=0,stake=50)],
        }
        for name, rows in data.items():
            with (Path(folder)/name).open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=rows[0].keys())
                writer.writeheader(); writer.writerows(rows)

    def test_refunds_and_timestamp_units(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder)
            bundle=c.convert(folder,GUILD,'fixture',{'123'})
            self.assertEqual(bundle['wallets'][0]['balance'],500)
            self.assertEqual(bundle['wallets'][0]['seededAt'],1000)
            self.assertEqual(bundle['markets'][0]['bets'][0]['payout'],50)
            self.assertEqual(bundle['markets'][0]['status'],'refunded')

    def test_paid_history(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder,'resolved','payout')
            bundle=c.convert(folder,GUILD,'fixture',{'123'})
            self.assertEqual(bundle['markets'][0]['winner'],'alpha')

    def test_open_books_and_unknown_states_rejected(self):
        for state in ['open','frozen','unknown']:
            with tempfile.TemporaryDirectory() as folder:
                self.fixture(folder,state)
                with self.assertRaises(ValueError): c.convert(folder,GUILD,'fixture',{'123'})

    def test_explicit_game_channels_required(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder)
            with self.assertRaisesRegex(ValueError,'channel IDs'): c.convert(folder,GUILD,'fixture')

    def test_partial_payout_rejected_without_manufacturing_gold(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fixture(folder,'resolved','payout',40)
            with self.assertRaisesRegex(ValueError,'reconcile'): c.convert(folder,GUILD,'fixture',{'123'})

if __name__ == '__main__': unittest.main()
