/// <reference types="vite/client" />

/**
 * The release version, replaced at build time by Vite's `define` with the
 * contents of the root VERSION file — the same file `app/core/version.py`
 * reads for `/health`. Declared here because it is a compile-time
 * substitution, not a runtime global, so TypeScript has no other way to
 * know it exists.
 */
declare const __APP_VERSION__: string;
