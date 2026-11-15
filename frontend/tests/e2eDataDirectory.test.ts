import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ensureRunningE2eDataDirectory, validateE2eDataDirectory } from '../e2e/global-setup'

let temporaryRoot: string
beforeEach(() => { temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ndr-e2e-directory-test-')) })
afterEach(() => {
  // This test owns precisely the mkdtemp directory and never starts a server.
  fs.rmSync(temporaryRoot, { recursive: true, force: true })
})

describe('E2E directory ownership', () => {
  it('accepts a new isolated path and an empty directory without touching sibling runs', () => {
    const sibling = path.join(temporaryRoot, '.e2e', 'other-run')
    fs.mkdirSync(sibling, { recursive: true })
    fs.writeFileSync(path.join(sibling, 'evidence.txt'), 'keep')
    const target = path.join(temporaryRoot, '.e2e', 'new-run')
    expect(validateE2eDataDirectory('.e2e/new-run', temporaryRoot)).toBe(target)
    fs.mkdirSync(target)
    expect(validateE2eDataDirectory(target, temporaryRoot)).toBe(target)
    expect(fs.readFileSync(path.join(sibling, 'evidence.txt'), 'utf8')).toBe('keep')
  })

  it('rejects populated directory reuse before startup, preserving every existing file', () => {
    const target = path.join(temporaryRoot, '.e2e', 'old-run')
    fs.mkdirSync(target, { recursive: true })
    fs.writeFileSync(path.join(target, 'library.db'), 'do not delete')
    expect(() => validateE2eDataDirectory(target, temporaryRoot)).toThrow('非空')
    expect(validateE2eDataDirectory(target, temporaryRoot, true)).toBe(target)
    expect(fs.readFileSync(path.join(target, 'library.db'), 'utf8')).toBe('do not delete')
  })

  it('rejects the shared root, traversal, sibling paths and files as directories', () => {
    for (const target of ['.e2e', '.e2e/../data', '../other', '.e2e-sibling/run']) {
      expect(() => validateE2eDataDirectory(target, temporaryRoot)).toThrow('必须在')
    }
    fs.mkdirSync(path.join(temporaryRoot, '.e2e'))
    fs.writeFileSync(path.join(temporaryRoot, '.e2e', 'file'), 'keep')
    expect(() => validateE2eDataDirectory('.e2e/file', temporaryRoot)).toThrow('不是目录')
  })

  it('rejects junctions or symlinks within the isolation root', () => {
    const target = path.join(temporaryRoot, 'outside')
    fs.mkdirSync(target)
    fs.mkdirSync(path.join(temporaryRoot, '.e2e'))
    fs.symlinkSync(target, path.join(temporaryRoot, '.e2e', 'linked'), 'junction')
    expect(() => validateE2eDataDirectory('.e2e/linked/run', temporaryRoot)).toThrow('符号链接')
  })

  it('globalSetup only validates and ensures the current directory after the server starts', () => {
    const current = path.join(temporaryRoot, '.e2e', 'running')
    const sibling = path.join(temporaryRoot, '.e2e', 'other-run')
    fs.mkdirSync(current, { recursive: true })
    fs.mkdirSync(sibling)
    fs.writeFileSync(path.join(current, 'library.db'), 'running test database')
    fs.writeFileSync(path.join(sibling, 'screenshot.png'), 'other run evidence')
    ensureRunningE2eDataDirectory(current, temporaryRoot)
    expect(fs.readFileSync(path.join(current, 'library.db'), 'utf8')).toBe('running test database')
    expect(fs.readFileSync(path.join(sibling, 'screenshot.png'), 'utf8')).toBe('other run evidence')
  })
})
