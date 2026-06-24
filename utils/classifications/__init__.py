"""Offline player-classification framework (trigger + factors + data-requirements per
classification). Pure logic here is DB- and mgz-free so it unit-tests cleanly; the runner
(runner.py) is the only module that touches replays, the network, or the database."""
