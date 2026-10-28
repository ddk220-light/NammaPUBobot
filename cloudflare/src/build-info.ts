import pkg from '../package.json';

// Injected by scripts/build-metadata.mjs. Direct development/test runs say unbuilt,
// rather than attributing an unversioned bundle to the last deployed revision.
declare const __SOURCE_REVISION__: string;
declare const __SOURCE_DIRTY__: boolean;
declare const __SOURCE_TREE_HASH__: string;
export const buildInfo = Object.freeze({
  version: pkg.version,
  sourceRevision: typeof __SOURCE_REVISION__ === 'undefined' ? 'unbuilt' : __SOURCE_REVISION__,
  sourceDirty: typeof __SOURCE_DIRTY__ === 'undefined' ? true : __SOURCE_DIRTY__,
  sourceTreeHash: typeof __SOURCE_TREE_HASH__ === 'undefined' ? null : __SOURCE_TREE_HASH__,
});
