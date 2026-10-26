import { describe, expect, it } from 'vitest';
import { rateTeams, type Player, type RatingSettings } from '../src/domain/rating';
import fixtures from './fixtures/legacy-m4.json';
describe('legacy rating compatibility', () => {
  for (const [index, fixture] of fixtures.rating.entries())
    it(`${fixture.engine} calculation ${index}`, () => {
      expect(
        rateTeams(
          fixture.teams as [Player[], Player[]],
          fixture.winner as 0 | 1 | null,
          fixture.settings as RatingSettings,
          fixture.engine as 'aoe2',
        ),
      ).toEqual(fixture.expected);
    });
});
