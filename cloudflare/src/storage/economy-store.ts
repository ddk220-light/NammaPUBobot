import type { Queue, Settings } from '../domain/config';
import type { Match } from '../domain/play';
import { PlayError } from '../domain/play';
import type { EconomyImport } from '../domain/economy-import';
import {
  WEEK,
  nextThursday,
  stakeOptions,
  taxAmount,
  type Book,
  type Bet,
} from '../domain/economy';

export function createEconomyTables(sql: SqlStorage) {
  sql.exec(
    'CREATE TABLE IF NOT EXISTS bet_start_evidence(book_id TEXT PRIMARY KEY,started_at INTEGER NOT NULL,observed_at INTEGER NOT NULL,event_id TEXT NOT NULL)',
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS imported_predictions(source_id TEXT NOT NULL,market_id TEXT NOT NULL,game_id TEXT NOT NULL,config TEXT NOT NULL,PRIMARY KEY(source_id,market_id))`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS wallets(game_id TEXT NOT NULL,user_id TEXT NOT NULL,balance INTEGER NOT NULL CHECK(balance>=0 AND balance<=9007199254740991),seeded_at INTEGER NOT NULL,PRIMARY KEY(game_id,user_id))`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS gold_ledger(id TEXT PRIMARY KEY,game_id TEXT NOT NULL,user_id TEXT NOT NULL,amount INTEGER NOT NULL,kind TEXT NOT NULL,book_id TEXT,balance_after INTEGER NOT NULL,created_at INTEGER NOT NULL)`,
  );
  sql.exec(`CREATE INDEX IF NOT EXISTS ledger_user ON gold_ledger(game_id,user_id,created_at)`);
  sql.exec(
    `CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY,match_id TEXT NOT NULL,game_id TEXT NOT NULL,queue_id TEXT NOT NULL,round INTEGER NOT NULL,status TEXT NOT NULL,opened_at INTEGER NOT NULL,closed_at INTEGER,result TEXT,reason TEXT,burned INTEGER NOT NULL DEFAULT 0,UNIQUE(match_id,round))`,
  );
  sql.exec(`CREATE INDEX IF NOT EXISTS book_match ON books(match_id,round DESC)`);
  sql.exec(
    `CREATE TABLE IF NOT EXISTS bets(book_id TEXT NOT NULL,user_id TEXT NOT NULL,side TEXT NOT NULL,stake INTEGER NOT NULL CHECK(stake>0),PRIMARY KEY(book_id,user_id))`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS economy_clocks(game_id TEXT PRIMARY KEY,due INTEGER NOT NULL,activated_at INTEGER NOT NULL,minute INTEGER NOT NULL)`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS tax_runs(game_id TEXT NOT NULL,cutoff INTEGER NOT NULL,total INTEGER NOT NULL,holders INTEGER NOT NULL,PRIMARY KEY(game_id,cutoff))`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS bet_participation(game_id TEXT NOT NULL,user_id TEXT NOT NULL,book_id TEXT NOT NULL,closed_at INTEGER NOT NULL,PRIMARY KEY(book_id,user_id))`,
  );
  sql.exec(
    `CREATE TABLE IF NOT EXISTS economy_imports(source_id TEXT PRIMARY KEY,digest TEXT NOT NULL,game_id TEXT NOT NULL,imported_at INTEGER NOT NULL,counts TEXT NOT NULL)`,
  );
}

/** All mutators are called inside the community transaction. No network side effects. */
export class EconomyStore {
  constructor(private sql: SqlStorage) {}
  change(
    game: string,
    user: string,
    amount: number,
    kind: string,
    id: string,
    book: string | null = null,
    now = Date.now(),
  ) {
    if (this.sql.exec('SELECT 1 FROM gold_ledger WHERE id=?', id).toArray().length) return false;
    if (!Number.isSafeInteger(amount)) throw new PlayError('Invalid gold amount.');
    const balance = this.balance(game, user) + amount;
    if (!Number.isSafeInteger(balance) || balance < 0)
      throw new PlayError('Not enough gold for this stake.');
    this.sql.exec(
      'INSERT INTO wallets VALUES(?,?,?,?) ON CONFLICT(game_id,user_id) DO UPDATE SET balance=excluded.balance',
      game,
      user,
      balance,
      now,
    );
    this.sql.exec(
      'INSERT INTO gold_ledger VALUES(?,?,?,?,?,?,?,?)',
      id,
      game,
      user,
      amount,
      kind,
      book,
      balance,
      now,
    );
    return true;
  }
  balance(game: string, user: string) {
    return (
      this.sql
        .exec<{ balance: number }>(
          'SELECT balance FROM wallets WHERE game_id=? AND user_id=?',
          game,
          user,
        )
        .toArray()[0]?.balance ?? 0
    );
  }
  seed(game: string, user: string, now = Date.now()) {
    if (
      this.sql.exec('SELECT 1 FROM wallets WHERE game_id=? AND user_id=?', game, user).toArray()
        .length
    )
      return;
    this.change(game, user, 500, 'seed', `seed:${game}:${user}`, null, now);
  }
  book(match: string): Book | undefined {
    return this.sql
      .exec<Book>('SELECT * FROM books WHERE match_id=? ORDER BY round DESC LIMIT 1', match)
      .toArray()[0];
  }
  bets(book: string) {
    return this.sql
      .exec<Bet>('SELECT user_id,side,stake FROM bets WHERE book_id=? ORDER BY user_id', book)
      .toArray();
  }
  bet(book: string, user: string): Bet | undefined {
    return this.sql
      .exec<Bet>('SELECT user_id,side,stake FROM bets WHERE book_id=? AND user_id=?', book, user)
      .toArray()[0];
  }
  pools(book: string) {
    const b = this.bets(book);
    return {
      alpha: b.filter((b) => b.side === 'alpha').reduce((n, b) => n + b.stake, 0),
      beta: b.filter((b) => b.side === 'beta').reduce((n, b) => n + b.stake, 0),
    };
  }
  open(m: Match) {
    if (m.test || !m.queue.modules.predictions || !m.queue.modules.gold) return;
    const old = this.book(m.id);
    if (old && ['open', 'frozen', 'settled'].includes(old.status)) return;
    this.sql.exec(
      'INSERT INTO books(id,match_id,game_id,queue_id,round,status,opened_at) VALUES(?,?,?,?,?,?,?)',
      crypto.randomUUID(),
      m.id,
      m.queue.gameId,
      m.queue.id,
      (old?.round ?? 0) + 1,
      'open',
      Date.now(),
    );
    for (const p of m.roster) this.seed(m.queue.gameId, p.id);
  }
  place(
    m: Match,
    user: string,
    side: 'alpha' | 'beta',
    stake: number,
    interaction: string,
    enabled: boolean,
  ) {
    const b = this.book(m.id);
    if (!enabled || !b || b.status !== 'open' || !['playing', 'check_in'].includes(m.state))
      throw new PlayError('Betting is closed.');
    this.assertPreGame(m);
    this.seed(m.queue.gameId, user);
    const own = m.teams.findIndex((t) => t.some((p) => p.id === user));
    if (own >= 0 && side !== (own === 0 ? 'alpha' : 'beta'))
      throw new PlayError('Match players can only back their own team.');
    const old = this.bets(b.id).find((b) => b.user_id === user);
    if (old && old.side !== side)
      throw new PlayError('Cancel your current bet before changing sides.');
    if (
      !Number.isSafeInteger(stake) ||
      !stakeOptions(this.balance(b.game_id, user)).includes(stake)
    )
      throw new PlayError('Your balance changed. Open Place bet for current amounts.');
    if (!old && this.bets(b.id).length >= 1000)
      throw new PlayError('This prediction pool is full.');
    if (
      this.bets(b.id).reduce((n, b) => n + BigInt(b.stake), 0n) + BigInt(stake) >
      BigInt(Number.MAX_SAFE_INTEGER)
    )
      throw new PlayError('This prediction pool has reached its gold limit.');
    if (!this.change(b.game_id, user, -stake, 'bet', `bet:${interaction}`, b.id))
      return this.balance(b.game_id, user);
    this.sql.exec(
      'INSERT INTO bets VALUES(?,?,?,?) ON CONFLICT(book_id,user_id) DO UPDATE SET stake=stake+excluded.stake',
      b.id,
      user,
      side,
      stake,
    );
    return this.balance(b.game_id, user);
  }
  cancel(m: Match, user: string, id: string) {
    const b = this.book(m.id);
    if (!b || b.status !== 'open') throw new PlayError('Betting is closed.');
    this.assertPreGame(m);
    const bet = this.bets(b.id).find((b) => b.user_id === user);
    if (!bet) return;
    this.change(b.game_id, user, bet.stake, 'refund', `cancel:${id}`, b.id);
    this.sql.exec('DELETE FROM bets WHERE book_id=? AND user_id=?', b.id, user);
  }
  private assertPreGame(m: Match) {
    const b = m.lobby;
    // A provisional association is informational; manual games keep captain-controlled closing.
    if (
      !b ||
      (b.availability && b.availability !== 'available') ||
      !['pending', 'observed', 'unavailable'].includes(b.state) ||
      (!b.rosterVerified && !b.confirmedBy)
    )
      return;
    if (
      b.state !== 'observed' ||
      !b.observation ||
      ['started', 'finished'].includes(b.observation.phase) ||
      Date.now() - (b.lastSuccessAt ?? 0) > 60000
    )
      throw new PlayError(
        'Bet changes are paused until fresh pre-game lobby status is available. A captain can still close betting.',
      );
  }
  startObserved(m: Match, startedAt: number, eventId: string) {
    const b = this.book(m.id);
    if (!b || ['settled', 'refunded'].includes(b.status)) return;
    const previous = this.sql
      .exec<{ started_at: number }>(
        'SELECT started_at FROM bet_start_evidence WHERE book_id=?',
        b.id,
      )
      .toArray()[0];
    if (previous) return;
    this.sql.exec(
      'INSERT INTO bet_start_evidence VALUES(?,?,?,?)',
      b.id,
      startedAt,
      Date.now(),
      eventId,
    );
    const late = this.sql
      .exec(
        "SELECT 1 FROM gold_ledger WHERE book_id=? AND created_at>=? AND (kind='bet' OR id LIKE 'cancel:%') LIMIT 1",
        b.id,
        startedAt,
      )
      .toArray().length;
    if (late || b.opened_at > startedAt) {
      this.sql.exec('DELETE FROM bet_participation WHERE book_id=?', b.id);
      this.refund(b, 'late_start_evidence');
      return;
    }
    if (b.status === 'open') this.freeze(m, startedAt);
  }
  freeze(m: Match, cutoff = Date.now()) {
    const b = this.book(m.id);
    if (!b || b.status !== 'open') throw new PlayError('Betting is already closed.');
    this.sql.exec(
      "UPDATE books SET status='frozen',closed_at=? WHERE id=? AND status='open'",
      cutoff,
      b.id,
    );
    for (const bet of this.bets(b.id))
      this.sql.exec(
        'INSERT OR IGNORE INTO bet_participation VALUES(?,?,?,?)',
        b.game_id,
        bet.user_id,
        b.id,
        cutoff,
      );
    const pools = this.pools(b.id);
    if (!pools.alpha || !pools.beta) this.refund(b, 'one_sided');
  }
  private refund(b: Book, reason: string) {
    if (['settled', 'refunded'].includes(b.status)) return;
    for (const bet of this.bets(b.id))
      this.change(
        b.game_id,
        bet.user_id,
        bet.stake,
        'refund',
        `refund:${b.id}:${bet.user_id}`,
        b.id,
      );
    this.sql.exec(
      "UPDATE books SET status='refunded',closed_at=COALESCE(closed_at,?),reason=? WHERE id=?",
      Date.now(),
      reason,
      b.id,
    );
  }
  rosterChanged(m: Match) {
    const b = this.book(m.id);
    if (b && b.status === 'open') {
      this.refund(b, 'roster_changed');
      this.open(m);
    } else if (b?.status === 'frozen') this.refund(b, 'roster_changed_after_close');
  }
  markTest(m: Match) {
    const books = this.sql.exec<Book>('SELECT * FROM books WHERE match_id=?', m.id).toArray();
    if (books.some((b) => b.status === 'settled'))
      throw new PlayError('This match already has settled predictions and cannot become a test.');
    for (const book of books) {
      this.refund(book, 'test_match');
      this.sql.exec('DELETE FROM bet_participation WHERE book_id=?', book.id);
    }
  }
  finish(m: Match, result: 'alpha' | 'beta' | 'draw' | 'abort') {
    if (m.test) {
      this.markTest(m);
      return;
    }
    const b = this.book(m.id);
    if (b && !['settled', 'refunded'].includes(b.status)) {
      const unverifiedStart =
        !!m.lobby &&
        (!m.lobby.availability || m.lobby.availability === 'available') &&
        ['pending', 'observed', 'unavailable'].includes(m.lobby.state) &&
        (m.lobby.rosterVerified || !!m.lobby.confirmedBy) &&
        !this.sql.exec('SELECT 1 FROM bet_start_evidence WHERE book_id=?', b.id).toArray().length;
      if (b.status === 'open' || result === 'draw' || result === 'abort' || unverifiedStart)
        this.refund(
          b,
          unverifiedStart && result !== 'abort' && result !== 'draw'
            ? 'unverified_game_start'
            : m.state === 'check_in_failed'
              ? 'check_in_failed'
              : result === 'abort'
                ? 'match_cancelled'
                : result === 'draw'
                  ? 'draw'
                  : 'reported_while_open',
        );
      else {
        const pools = this.pools(b.id),
          total = pools.alpha + pools.beta,
          winning = pools[result];
        if (!winning || winning === total) this.refund(b, 'one_sided');
        else {
          let paid = 0;
          for (const bet of this.bets(b.id).filter((b) => b.side === result)) {
            const amount = Number((BigInt(bet.stake) * BigInt(total)) / BigInt(winning));
            this.change(
              b.game_id,
              bet.user_id,
              amount,
              'payout',
              `payout:${b.id}:${bet.user_id}`,
              b.id,
            );
            paid += amount;
          }
          this.sql.exec(
            "UPDATE books SET status='settled',result=?,burned=? WHERE id=?",
            result,
            total - paid,
            b.id,
          );
        }
      }
    }
    if (result !== 'abort' && m.queue.modules.gold)
      for (const p of m.roster) {
        this.seed(m.queue.gameId, p.id);
        const reward = Math.min(100, Math.max(0, 500 - this.balance(m.queue.gameId, p.id)));
        this.change(m.queue.gameId, p.id, reward, 'match_reward', `reward:${m.id}:${p.id}`);
      }
  }
  configure(settings: Settings, now = Date.now()) {
    for (const game of settings.games) {
      const queues = settings.queues.filter(
        (q) => q.gameId === game.id && q.modules.gold && q.economy?.taxEnabled,
      );
      if (!queues.length) {
        this.sql.exec('DELETE FROM economy_clocks WHERE game_id=?', game.id);
        continue;
      }
      const minute = queues[0].economy?.taxMinuteUtc ?? 0;
      const old = this.sql
        .exec<{ minute: number }>('SELECT minute FROM economy_clocks WHERE game_id=?', game.id)
        .toArray()[0];
      if (!old)
        this.sql.exec(
          'INSERT INTO economy_clocks VALUES(?,?,?,?)',
          game.id,
          nextThursday(now + WEEK - 1, minute),
          now,
          minute,
        );
      else if (old.minute !== minute)
        this.sql.exec(
          'UPDATE economy_clocks SET due=?,minute=? WHERE game_id=?',
          nextThursday(now, minute),
          minute,
          game.id,
        );
    }
  }
  tax(now = Date.now()) {
    for (const clock of this.sql
      .exec<{ game_id: string; due: number; activated_at: number; minute: number }>(
        'SELECT * FROM economy_clocks WHERE due<=? LIMIT 8',
        now,
      )
      .toArray()) {
      for (let week = 0, cutoff = clock.due; cutoff <= now && week < 8; week++, cutoff += WEEK) {
        if (
          !this.sql
            .exec('SELECT 1 FROM tax_runs WHERE game_id=? AND cutoff=?', clock.game_id, cutoff)
            .toArray().length
        ) {
          const opportunity = this.sql
            .exec(
              'SELECT 1 FROM books WHERE game_id=? AND opened_at<? AND (closed_at IS NULL OR closed_at>?) LIMIT 1',
              clock.game_id,
              cutoff,
              cutoff - WEEK,
            )
            .toArray().length;
          const participated = new Set(
            this.sql
              .exec<{ user_id: string }>(
                'SELECT DISTINCT user_id FROM bet_participation WHERE game_id=? AND closed_at>=? AND closed_at<?',
                clock.game_id,
                cutoff - WEEK,
                cutoff,
              )
              .toArray()
              .map((r) => r.user_id),
          );
          let total = 0,
            holders = 0;
          if (opportunity && cutoff >= clock.activated_at + WEEK)
            for (const w of this.sql
              .exec<{ user_id: string; balance: number; seeded_at: number }>(
                'SELECT * FROM wallets WHERE game_id=?',
                clock.game_id,
              )
              .toArray()) {
              const amount = taxAmount(w.balance, w.seeded_at, cutoff, participated.has(w.user_id));
              if (amount) {
                this.change(
                  clock.game_id,
                  w.user_id,
                  -amount,
                  'tax',
                  `tax:${clock.game_id}:${cutoff}:${w.user_id}`,
                  null,
                  cutoff,
                );
                total += amount;
                holders++;
              }
            }
          this.sql.exec(
            'INSERT INTO tax_runs VALUES(?,?,?,?)',
            clock.game_id,
            cutoff,
            total,
            holders,
          );
        }
        this.sql.exec(
          'UPDATE economy_clocks SET due=? WHERE game_id=?',
          cutoff + WEEK,
          clock.game_id,
        );
      }
    }
  }
  records(game?: string, user?: string) {
    const filter = game ? ' WHERE game_id=?' : '',
      args = game ? [game] : [];
    return {
      wallets: this.sql
        .exec('SELECT * FROM wallets' + filter + ' ORDER BY balance DESC LIMIT 100', ...args)
        .toArray(),
      books: this.sql
        .exec('SELECT * FROM books' + filter + ' ORDER BY opened_at DESC LIMIT 50', ...args)
        .toArray(),
      ledger: this.sql
        .exec(
          'SELECT * FROM gold_ledger' +
            (game && user ? ' WHERE game_id=? AND user_id=?' : filter) +
            ' ORDER BY created_at DESC LIMIT 100',
          ...args,
          ...(game && user ? [user] : []),
        )
        .toArray(),
      importedPredictions: this.sql
        .exec<{ config: string }>(
          'SELECT config FROM imported_predictions' + filter + ' ORDER BY market_id DESC LIMIT 100',
          ...args,
        )
        .toArray()
        .map((r) => JSON.parse(r.config) as NonNullable<EconomyImport['markets']>[number]),
      taxRuns: this.sql
        .exec('SELECT * FROM tax_runs' + filter + ' ORDER BY cutoff DESC LIMIT 20', ...args)
        .toArray(),
    };
  }
  importPreview(game: string, bundle: EconomyImport) {
    const users = new Set(bundle.wallets.map((w) => w.userId)),
      ids = new Set(bundle.ledger.map((l) => l.id));
    if (users.size !== bundle.wallets.length || ids.size !== bundle.ledger.length)
      throw new PlayError('Duplicate economy identities.');
    if (bundle.ledger.some((l) => !users.has(l.userId)))
      throw new PlayError('A ledger row lacks its wallet.');
    for (const wallet of bundle.wallets) {
      let total = 0n;
      for (const row of bundle.ledger
        .filter((l) => l.userId === wallet.userId)
        .sort((a, b) => a.at - b.at)) {
        total += BigInt(row.amount);
        if (total < 0n || total > BigInt(Number.MAX_SAFE_INTEGER))
          throw new PlayError('Imported ledger has an invalid running balance.');
      }
      if (total !== BigInt(wallet.balance))
        throw new PlayError('Imported balances must equal their complete ledger totals.');
    }
    const markets = bundle.markets ?? [];
    if (new Set(markets.map((m) => m.id)).size !== markets.length)
      throw new PlayError('Duplicate imported market identity.');
    for (const market of markets) {
      if (market.closedAt < market.openedAt || (market.status === 'settled' && !market.winner))
        throw new PlayError('Imported prediction has invalid final metadata.');
      if (
        new Set(market.bets.map((b) => b.userId)).size !== market.bets.length ||
        market.bets.some((b) => !users.has(b.userId))
      )
        throw new PlayError('Imported prediction requires unique source wallets.');
      const pool = market.bets.reduce((n, b) => n + BigInt(b.stake), 0n);
      const winningPool = market.bets
        .filter((b) => b.side === market.winner)
        .reduce((n, b) => n + BigInt(b.stake), 0n);
      for (const bet of market.bets) {
        const expected =
          market.status === 'refunded'
            ? BigInt(bet.stake)
            : bet.side === market.winner && winningPool
              ? (pool * BigInt(bet.stake)) / winningPool
              : 0n;
        if (expected !== BigInt(bet.payout))
          throw new PlayError('Imported prediction payouts do not reconcile.');
      }
    }
    const existing = this.sql
      .exec('SELECT 1 FROM wallets WHERE game_id=? LIMIT 1', game)
      .toArray().length;
    if (
      existing &&
      !this.sql.exec('SELECT 1 FROM economy_imports WHERE source_id=?', bundle.sourceId).toArray()
        .length
    )
      throw new PlayError('Economy import requires an empty destination game.');
    return {
      wallets: bundle.wallets.length,
      ledger: bundle.ledger.length,
      markets: markets.length,
      scope: 'one community and one game; no balance duplication',
    };
  }
  importApply(
    game: string,
    bundle: EconomyImport,
    digest: string,
  ): { alreadyImported: boolean; counts: { wallets: number; ledger: number; scope: string } } {
    const old = this.sql
      .exec<{ digest: string; game_id: string; counts: string }>(
        'SELECT * FROM economy_imports WHERE source_id=?',
        bundle.sourceId,
      )
      .toArray()[0];
    if (old) {
      if (old.digest !== digest || old.game_id !== game)
        throw new PlayError('This source was imported with different data or scope.');
      return { alreadyImported: true, counts: JSON.parse(old.counts) };
    }
    const counts = this.importPreview(game, bundle);
    const markets = bundle.markets ?? [];
    if (new Set(markets.map((m) => m.id)).size !== markets.length)
      throw new PlayError('Duplicate imported market identity.');
    for (const market of markets) {
      if (market.bets.some((b) => !bundle.wallets.some((w) => w.userId === b.userId)))
        throw new PlayError('Imported prediction lacks its source wallet.');
      this.sql.exec(
        'INSERT INTO imported_predictions VALUES(?,?,?,?)',
        bundle.sourceId,
        market.id,
        game,
        JSON.stringify(market),
      );
    }
    for (const wallet of bundle.wallets) {
      let balance = 0;
      const rows = bundle.ledger
        .filter((l) => l.userId === wallet.userId)
        .sort((a, b) => a.at - b.at);
      for (const row of rows) {
        balance += row.amount;
        if (!Number.isSafeInteger(balance) || balance < 0)
          throw new PlayError('Imported ledger has an invalid running balance.');
        this.sql.exec(
          'INSERT INTO gold_ledger VALUES(?,?,?,?,?,?,?,?)',
          `import:${bundle.sourceId}:${row.id}`,
          game,
          row.userId,
          row.amount,
          row.kind,
          null,
          balance,
          row.at,
        );
      }
      this.sql.exec(
        'INSERT INTO wallets VALUES(?,?,?,?)',
        game,
        wallet.userId,
        wallet.balance,
        wallet.seededAt,
      );
    }
    this.sql.exec(
      'INSERT INTO economy_imports VALUES(?,?,?,?,?)',
      bundle.sourceId,
      digest,
      game,
      Date.now(),
      JSON.stringify(counts),
    );
    return { alreadyImported: false, counts };
  }
}
