// Pin the timezone so the date formatters render deterministically regardless
// of the host machine. This lives in its own setup file, listed first, because
// ESM hoists imports: an assignment sitting above an `import` in the same file
// would still run after that import's module graph has been evaluated.
process.env.TZ = "UTC"
