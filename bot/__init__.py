# -*- coding: utf-8 -*-

import time

from .main import update_qc_lang, update_rating_system, save_state, save_state_db
from .main import load_state
from .main import remove_players

from .queue_channel import QueueChannel
from .queues.pickup_queue import PickupQueue
from .queues.common import QueueResponses as Qr
from .match.match import Match
from .expire import expire
from .stats import stats
from .stats.noadds import noadds
from .exceptions import Exceptions as Exc
from .context import Context, SlashContext, SystemContext
from . import commands

from . import events
from . import utils
from . import civ_reconcile  # noqa: F401  (ensure_table side effect + the reconcile job instance)
from . import lobby  # noqa: F401  (lobbies ensure_table + the LobbyJobs instance)
from . import quiz  # noqa: F401  (quiz_* ensure_table + the QuizJobs instance)
from . import predictions  # noqa: F401  (prediction_* ensure_table + the PredictionJobs instance)
from . import replay_stats  # noqa: F401  (replay_* ensure_table + ReplayStatsJobs instance)
from . import classifications  # noqa: F401  (cls_* ensure_table side effect)
from . import derived  # noqa: F401  (game_stats/game_labels ensure_table side effect)


queue_channels = dict()  # {channel.id: QueueChannel()}

