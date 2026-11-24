export const meta = {
  name: 'rule-sweep',
  description: "Read a language's spec and binder section by section with a smaller model: every name-binding rule, held / new case / not followed",
  whenToUse: "Phase 2b of docs/rule-sources.md: args {lang, groups, sources, case_hint} from `benchmarks/rule_inventory.py --lang L --sweep-groups`; the strongest model then merges and verifies (AGENTS.md, 'Reading a language's rules: the sweep')",
  phases: [
    { title: 'Extract', detail: 'one agent per group of sections', model: 'sonnet' },
    { title: 'Gaps', detail: 'a second reader per group looks for rules the first missed, until a round finds none (2 at most)', model: 'sonnet' },
  ],
}

// args: {
//   lang: 'go',
//   groups: [{group: 'declarations', sections: ['spec Blocks', 'go/types resolver.go', ...]}, ...],
//   sources: 'how to find a section: "spec X" is <h2|h3|h4 id="X"> in /tmp/sources/.../go_spec.html; "go/types F" is ...',
//   case_hint: 'what a case project needs in this language (a go.mod, the package clause, ...)',
//   max_gap_rounds: 2,
// }
const A = args || {}
if (!A.lang || !Array.isArray(A.groups) || !A.groups.length) {
  throw new Error('args needs {lang, groups: [{group, sections}], sources, case_hint}')
}
const ROUNDS = A.max_gap_rounds ?? 2

const EDGE = { type: 'array', items: { type: 'string' }, minItems: 3, maxItems: 3 }
const ROWS = {
  type: 'object',
  properties: {
    rows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          section: { type: 'string', description: 'one of the group\'s sections, exactly as given' },
          rule: { type: 'string', description: 'the rule, one sentence, in the style of benchmarks/rule_inventory.py' },
          status: { type: 'string', enum: ['held', 'new', 'not_followed', 'no_binding_rule'] },
          cases: { type: 'array', items: { type: 'string' }, description: 'held: the ids of the existing conformance cases that hold it' },
          reason: { type: 'string', description: 'not_followed / no_binding_rule: why, one sentence' },
          quote: { type: 'string', description: 'the sentence or code of the source the rule is read from, verbatim, short' },
          new_case: {
            type: 'object',
            description: 'new: one small project for the rule',
            properties: {
              cid: { type: 'string', description: 'kebab-case id, unique, not an existing one' },
              rule: { type: 'string' },
              files: { type: 'object', additionalProperties: { type: 'string' } },
              expect: { type: 'array', items: EDGE },
              absent: { type: 'array', items: EDGE },
              source: { type: 'string', description: 'the section, and the binder function when one says it' },
              since: { type: 'string', description: 'the language version the rule needs, or empty' },
            },
            required: ['cid', 'rule', 'files', 'expect', 'absent', 'source'],
          },
        },
        required: ['section', 'rule', 'status', 'quote'],
      },
    },
  },
  required: ['rows'],
}

const COMMON = (g) => `You are reading the ${A.lang} language's own definition of how names are bound, for graphlore's native engine (a code graph that links each written name to the definition it means). Sections of your group "${g.group}":
${g.sections.map((s) => `- ${s}`).join('\n')}

Where the sources are: ${A.sources}

A rule of name binding says which declaration a written name means (an identifier, a selector \`x.f\`, a type name, a call, an import, a label), or where a declared name is visible, hidden or shadowed, or which of several candidates wins. Type errors, evaluation order, run-time behaviour and syntax alone are no such rule.

What graphlore already has (read these first; do not repeat what they hold):
- \`python benchmarks/rule_inventory.py --lang ${A.lang} --markdown\`: the rules already read, with the cases that hold them;
- the \`case("${A.lang}", ...)\` calls in \`benchmarks/conformance.py\`: every case, its rule and its project (\`grep -n 'case("${A.lang}"' benchmarks/conformance.py\`, then read the ones near your sections).

How a case is written (see the existing ones): \`files\` is a whole small project; \`expect\` the edges the rule says the graph has, \`absent\` the edges of the wrong meaning (another definition of the same name). An edge is [source, relation, target]: a definition's qualified name or its last parts (\`Type.Method\`, \`helper\`), or \`path:qualname\` for one of several of a name; relations are calls, references, reads, writes, inherits, implements, embeds. Give every new case an \`absent\` edge whenever the language offers a wrong meaning to pick. ${A.case_hint}

Rules for you:
- Write NO file of the repository and change no code. You may write scratch projects under /tmp/claude-0/sweep/${A.lang}-${g.group}/ only: compile each new case there with the language's compiler, and only return a case that compiles. You may build it with \`python -m graphlore_engine build DIR --out DIR/graph.json\` to see the qualnames the graph gives its definitions, so that your edge names match; never change what an edge expects to what the engine happens to produce: the expectation is the language's.
- Read every section of the group, the whole of it. Return one row per rule, and one row with status no_binding_rule (rule empty) for each section that holds none.
- held: an existing case already makes the rule's point; name its id. new: no case does; write one. not_followed: the engine cannot or should not follow it (say why: it needs a type checker's inference, it is about values, ...).
- quote is the source's own words (or code), short, so that the rule can be checked against it.`

function extract(g) {
  return agent(`${COMMON(g)}\n\nRead the sections and return every rule they state.`, {
    label: `extract:${g.group}`, phase: 'Extract', model: 'sonnet', schema: ROWS,
  })
}

async function huntGaps(first, g) {
  const rows = (first && first.rows) || []
  for (let round = 1; round <= ROUNDS; round++) {
    const listed = rows.map((r) => `- [${r.section}] (${r.status}) ${r.rule || '(no rule)'}`).join('\n')
    const more = await agent(`${COMMON(g)}\n\nAnother reader already listed these rows for the group:\n${listed}\n\nRead the same sections again, slowly, looking for what that list lacks: a rule stated in a sentence it skipped, a special case (blank identifier, generic parameter, method value, embedded field, shadowing in a nested block, a version-dependent rule), a binder function's branch no row covers, a section it gave no row. Return ONLY rows that are missing from the list (an empty list when nothing is).`, {
      label: `gaps:${g.group}:${round}`, phase: 'Gaps', model: 'sonnet', schema: ROWS,
    })
    const fresh = ((more && more.rows) || []).filter((r) => !rows.some((x) => x.section === r.section && x.rule === r.rule))
    log(`${g.group}: round ${round} of gap hunting added ${fresh.length} rows`)
    if (!fresh.length) break
    rows.push(...fresh)
  }
  return { group: g.group, sections: g.sections, rows }
}

const results = await pipeline(A.groups, (g) => extract(g), (first, g) => huntGaps(first, g))
const done = results.filter(Boolean)
const lost = A.groups.filter((g) => !done.some((d) => d.group === g.group)).map((g) => g.group)
if (lost.length) log(`groups with no result (rerun them): ${lost.join(', ')}`)
return { lang: A.lang, groups: done, lost }
