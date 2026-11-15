import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url)))
const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url)))
const runtime = manifest.dependencies
const allowedPrimitives = new Set(['@radix-ui/react-slot'])
const competing = /^(?:@base-ui\/|@headlessui\/|@mui\/|antd$|react-icons$|@heroicons\/|@tabler\/|gsap$|react-spring$|@react-spring\/|tw-animate-css$|animate\.css$)/
for (const name of Object.keys({ ...runtime, ...manifest.devDependencies })) {
  assert(!competing.test(name), `UI stack must stay single: ${name}`)
  assert(name !== 'framer-motion', 'Use motion/react; do not install its implementation separately')
  assert(name !== 'radix-ui', 'Install only the Radix primitives actually used')
  if (name.startsWith('@radix-ui/')) assert(allowedPrimitives.has(name), `Review unused primitive: ${name}`)
}

for (const name of ['react', 'react-dom', '@tanstack/react-query', 'zustand', 'motion', 'lucide-react', '@radix-ui/react-slot']) {
  const locations = Object.keys(lock.packages).filter(path => path === `node_modules/${name}` || path.endsWith(`/node_modules/${name}`))
  assert.equal(locations.length, 1, `Expected one installed copy of ${name}: ${locations.join(', ')}`)
}
for (const name of ['@dnd-kit/core', '@dnd-kit/sortable', '@radix-ui/react-slot', 'lucide-react', 'motion', 'zustand', 'react-resizable-panels']) {
  assert.match(runtime[name], /^\d+\.\d+\.\d+$/, `Pin the reviewed UI dependency ${name}`)
  assert.equal(runtime[name], lock.packages[`node_modules/${name}`].version, `Lock mismatch: ${name}`)
}
assert.match(lock.packages['node_modules/react'].version, /^18\./, 'React 18 compatibility must be preserved')
console.log('UI dependencies: one primitive, icon and animation stack; runtime copies and pinned versions verified.')
