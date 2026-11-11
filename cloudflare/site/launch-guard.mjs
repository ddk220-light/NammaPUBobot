// A search-indexed public launch needs a named operator and private ways to reach them.
const filled = (value) => typeof value === 'string' && value.trim() !== '';

/** Returns the identity fields that must be filled in before indexing is enabled. */
export function launchBlockers(identity) {
  const missing = [];
  if (!filled(identity.operator)) missing.push('identity.operator');
  if (!filled(identity.contact?.email)) missing.push('identity.contact.email');
  if (!filled(identity.contact?.supportServer)) missing.push('identity.contact.supportServer');
  return missing;
}
