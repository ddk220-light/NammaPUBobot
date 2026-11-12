import { describe, it, expect } from 'vitest';
import { identity } from '../src/identity';
import { launchBlockers } from '../site/launch-guard.mjs';
import { contactRoutes, legalArticles } from '../site/legal.mjs';

const unset = {
  ...identity,
  operator: null,
  contact: { email: null, supportServer: null, issues: `${identity.repository}/issues` },
};
const filled = {
  ...identity,
  operator: 'Example Operator',
  contact: {
    email: 'help@example.org',
    supportServer: 'https://discord.gg/example',
    issues: `${identity.repository}/issues`,
  },
};
const headings = (html: string) =>
  [...html.matchAll(/<h2[^>]*>([\s\S]*?)<\/h2>/g)].map((m) => m[1]);

describe('public legal pages', () => {
  it('blocks an indexed launch until operator, email and support server exist', () => {
    expect(launchBlockers(unset)).toEqual([
      'identity.operator',
      'identity.contact.email',
      'identity.contact.supportServer',
    ]);
    expect(launchBlockers({ ...filled, operator: '  ' })).toEqual(['identity.operator']);
    expect(launchBlockers(filled)).toEqual([]);
  });

  it('lists only the contact routes that exist', () => {
    expect(contactRoutes(unset).map(([label]: string[]) => label)).toEqual(['GitHub Issues']);
    const all = contactRoutes(filled);
    expect(all.map(([label]: string[]) => label)).toEqual([
      'Support Discord server',
      'Email',
      'GitHub Issues',
    ]);
    // The address itself is visible, selectable text, not hidden behind a label.
    expect(all[1][1]).toContain('>help@example.org</a>');
  });

  it('gives privacy and terms the required headings and a last-updated date', () => {
    for (const id of [unset, filled]) {
      const pages = legalArticles(id);
      const [privacyTitle, , privacy] = pages['/privacy/'];
      const [termsTitle, , terms] = pages['/terms/'];
      expect(privacyTitle).toBe('Privacy policy');
      expect(termsTitle).toBe('Terms of use');
      for (const body of [privacy, terms])
        expect(body).toMatch(/Last updated: <time datetime="\d{4}-\d{2}-\d{2}">/);
      expect(headings(privacy)).toEqual([
        'Who runs this',
        'What we store and why',
        "What we don't do",
        'Who else is involved',
        'How long we keep it',
        'Your choices and how to delete',
        'Children',
        'Changes to this policy',
        'Contact',
      ]);
      expect(headings(terms)).toEqual([
        'It is free',
        'What the service is',
        'Acceptable use',
        'Gold is play money',
        'Results and ratings',
        'No warranty',
        'Open source',
        'Changes to these terms',
        'Contact',
      ]);
      expect(privacy).toContain('/profile hide');
      expect(terms).toContain('GPL-3.0-only');
    }
  });

  it('removes pricing hedges and unconfirmed placeholders', () => {
    for (const id of [unset, filled])
      for (const [, , body] of Object.values(legalArticles(id)) as string[][]) {
        expect(body).not.toMatch(/free hosting forever|preview does not promise/i);
        expect(body).not.toContain('will be added when confirmed');
        expect(body).not.toContain(identity.internalName);
      }
  });

  it('names the operator only when the owner has supplied one', () => {
    expect(legalArticles(unset)['/about/'][2]).not.toContain('Example Operator');
    expect(legalArticles(filled)['/about/'][2]).toContain('Example Operator runs the hosted');
    expect(legalArticles(filled)['/privacy/'][2]).toContain('mailto:help@example.org');
    expect(legalArticles(unset)['/privacy/'][2]).not.toContain('mailto:');
    expect(legalArticles(unset)['/about/'][2]).toContain('id="contact"');
  });
});
