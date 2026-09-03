#!/usr/bin/env node
// Compiles every .yml/.js cube/view file in a directory using Cube's own
// schema-compiler, the same way Cube itself validates model/ on startup.
// Used by the semantic-model-agent as its "does this actually compile"
// check, mirroring how mcfly shells out to malloy-cli for Malloy.
//
// Usage: node scripts/validate-cube-schema.js <dir>
// Prints "SUCCESS" (exit 0) or "ERRORS:\n<details>" (exit 1).

const path = require('path');
const { FileRepository } = require('@cubejs-backend/shared');
const { compile } = require('@cubejs-backend/schema-compiler');

async function main() {
  const dir = process.argv[2];
  if (!dir) {
    console.error('ERRORS:\nUsage: node validate-cube-schema.js <dir>');
    process.exit(1);
  }

  // FileRepository.localPath() does path.join(process.cwd(), repositoryPath)
  // — an absolute repositoryPath would double up with cwd, so always hand
  // it a path relative to cwd instead.
  const repo = new FileRepository(path.relative(process.cwd(), path.resolve(dir)));
  try {
    await compile(repo, {});
    console.log('SUCCESS');
  } catch (error) {
    console.log(`ERRORS:\n${error.message || String(error)}`);
    process.exit(1);
  }
}

main();
