# Logging & Packaging Guidelines

> Guidelines for logging and native module packaging.

---

## Logging Guidelines

### Use Structured Logging

```typescript
// CORRECT
import log from 'electron-log';
log.info('Project created', { projectId: project.id, name: project.name });
log.error('Database error', { error: err.message });

// WRONG
console.log('Project created: ' + project.id);
```

### Scoped Logger Pattern

```typescript
// src/main/services/logger.ts
import log from 'electron-log';

log.transports.file.format = '[{y}-{m}-{d} {h}:{i}:{s}] [{level}] {text}';
log.transports.console.format = '[{level}] {text}';

export const logger = log;

// Usage in modules
import { logger as baseLogger } from '../../logger';

const logger = baseLogger.scope('project:create');
// Output: [project:create] Project created { projectId: '123' }

logger.info('Project created', { projectId: '123' });
```

### Redact Secret-Like Key Names

Log payload redaction must treat provider-key field names as secret values, including `api_key`, `*_api_key`, and `*_key` such as `codex_rin977_key` or `openai_api_key`. Logs may record the environment variable name in a neutral metadata field like `variable_name`, but must not record the variable value.

```typescript
// Correct: records the variable name without using a secret-like payload key.
logger.info('Codex env key imported', { variable_name: 'codex_rin977_key' });

// Wrong: would require redaction because the key name itself denotes a secret value.
logger.info('Codex env key imported', { codex_rin977_key: process.env.CODEX_RIN977_KEY });
```

### Log Levels

| Level   | Use Case            |
| ------- | ------------------- |
| `error` | Unexpected failures |
| `warn`  | Recoverable issues  |
| `info`  | Important events    |
| `debug` | Development details |

---

## Packaging Native Modules

Native modules like `better-sqlite3` require special configuration.

### Vite Config

```typescript
// vite.main.config.ts
export default defineConfig({
  build: {
    rollupOptions: {
      external: ['better-sqlite3'],
    },
  },
});
```

### Forge Config

```typescript
// forge.config.ts
const nativeModules = ['better-sqlite3', 'bindings', 'file-uri-to-path'];

const config = {
  packagerConfig: {
    asar: {
      unpack: '*.{node,dll}',
    },
    extraResource: ['./drizzle'],
  },
  rebuildConfig: {
    force: true,
  },
  hooks: {
    packageAfterCopy: async (_config, buildPath) => {
      const sourceModules = path.resolve(__dirname, 'node_modules');
      const destModules = path.resolve(buildPath, 'node_modules');

      await Promise.all(
        nativeModules.map(async (pkg) => {
          await cp(path.join(sourceModules, pkg), path.join(destModules, pkg), {
            recursive: true,
          });
        })
      );
    },
  },
};
```

### Fuses

```typescript
new FusesPlugin({
  [FuseV1Options.OnlyLoadAppFromAsar]: false, // Required!
});
```

---

## Summary

| Rule                             | Reason                     |
| -------------------------------- | -------------------------- |
| Use `logger.scope()`             | Module identification      |
| Structured logging               | Easier to search           |
| Mark native modules external     | Vite compatibility         |
| Copy native modules in Forge     | Runtime availability       |
| Set `OnlyLoadAppFromAsar: false` | Allow unpacked .node files |
