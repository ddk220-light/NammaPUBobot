/** Public individual Steam accounts (desktop instance + uint32 account ID). Never use Number. */
export function isSteamIndividualId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{17}$/.test(value) &&
    BigInt(value) >= 76561197960265728n &&
    BigInt(value) <= 76561202255233023n
  );
}
