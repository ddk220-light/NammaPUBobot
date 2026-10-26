"""Offline source-format checks; no database access."""
import importlib.util
import unittest
import tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('converter','scripts/convert-legacy.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
mapping={'ladderByChannel':{'123':'shared'},'mappings':[{'ladder':'shared','gameId':'4daebc0a-55e8-4d56-a17d-d94913644910','engine':'aoe2','queues':{'9':'81da571b-6a4a-4e5d-8e32-f4050df35a2d'},'initial':1000,'deviation':200}]}
def files():return {
    'player_ratings.csv':[dict(channel_id='123',user_id='100000000000000100',nick='Friend',rating='1048',deviation='200',wins='1',losses='0',draws='0',streak='1',last_ranked_match_at='1700000000')],
    'matches.csv':[dict(channel_id='123',match_id='1',queue_id='9',reported_at='1700000000',ranked='1',winner='0')],
    'match_players.csv':[dict(match_id='1',user_id='100000000000000100',team='0'),dict(match_id='1',user_id='100000000000000101',team='1')],
    'rating_history.csv':[dict(channel_id='123',id='1',user_id='100000000000000100',at='1700000000',rating_before='1000',rating_change='48',deviation_before='200',deviation_change='0',match_id='1',reason='Match result')],
}
class ConverterTests(unittest.TestCase):
    def test_reads_only_core_files_and_rejects_oversized_core(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            (folder/'gold.csv').write_text('not valid private data')
            (folder/'player_ratings.csv').write_text('user_id,rating\n100000000000000100,1000\n')
            self.assertEqual(list(c.read_files(folder)),['player_ratings.csv'])
            (folder/'player_ratings.csv').write_bytes(b'x'*5_000_001)
            with self.assertRaisesRegex(ValueError,'exceeds 5 MB'): c.read_files(folder)

    def test_namma(self):
        b=c.convert(files(),'nammapubobot',mapping,'history','fixture',None,None)
        self.assertEqual(b['matches'][0]['result'],'alpha');self.assertEqual(b['changes'][0]['after'],1048)
        self.assertEqual(b['players'][0]['lastRankedAt'],1700000000000)
        self.assertEqual(b['mappings'][0]['engine'],'aoe2')
    def test_mapping_required(self):
        f=files();f['player_ratings.csv'][0]['channel_id']='unknown'
        with self.assertRaisesRegex(ValueError,'explicit ladder'):c.convert(f,'nammapubobot',mapping,'seed','fixture',None,None)
    def test_pubobot_ambiguous_winner_and_timezone(self):
        f=files();pubo={'qc_players.csv':f['player_ratings.csv'],'qc_matches.csv':[dict(match_id='1',queue='9',at='2026-10-01 12:00:00',winner_team='NULL')],'qc_player_matches.csv':f['match_players.csv'],'qc_rating_history.csv':[{**f['rating_history.csv'][0],'at':'2026-10-01 12:00:00'}]}
        with self.assertRaisesRegex(ValueError,'timezone'):c.convert(pubo,'pubobot',mapping,'history','fixture',None,'draw')
        with self.assertRaisesRegex(ValueError,'NULL'):c.convert(pubo,'pubobot',mapping,'history','fixture','UTC',None)
        b=c.convert(pubo,'pubobot',mapping,'history','fixture','UTC','draw');self.assertEqual(b['matches'][0]['result'],'draw')
        self.assertIsNotNone(b['players'][0]['lastRankedAt'])
    def test_shared_source_channels(self):
        m={**mapping,'ladderByChannel':{'123':'shared','124':'shared'}}
        f=files();f['matches.csv'][0]['channel_id']='124';b=c.convert(f,'nammapubobot',m,'history','fixture',None,None)
        self.assertEqual(b['players'][0]['ladder'],b['matches'][0]['ladder'])
    def test_unranked_namma_aborts(self):
        f=files();f['matches.csv'][0]['ranked']='0';b=c.convert(f,'nammapubobot',mapping,'history','fixture',None,None);self.assertEqual(b['matches'][0]['result'],'abort')
if __name__=='__main__':unittest.main()
