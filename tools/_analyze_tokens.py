#!/usr/bin/env python3
"""One-shot analysis of session transcripts for cost tracker design."""
import json, os, glob

session_dir = os.path.expanduser('~/.claude/projects/-Users-holyty')
files = sorted(glob.glob(os.path.join(session_dir, '*.jsonl')))

model_counts = {}
total_input = 0
total_output = 0
total_cache_read = 0
total_cache_create = 0
session_stats = []
tool_counts = {}

for f in files:
    st = {'input': 0, 'output': 0, 'cache_read': 0, 'cache_create': 0,
          'tool_calls': 0, 'user_msgs': 0, 'assistant_msgs': 0,
          'thinking_tokens': 0, 'tool_names': {}}
    with open(f) as fh:
        for line in fh:
            try:
                e = json.loads(line.strip())
            except:
                continue
            t = e.get('type', '')
            if t == 'assistant' and 'message' in e:
                usage = e['message'].get('usage', {})
                if usage.get('input_tokens', 0) > 0 or usage.get('output_tokens', 0) > 0:
                    st['input'] += usage.get('input_tokens', 0)
                    st['output'] += usage.get('output_tokens', 0)
                    st['cache_read'] += usage.get('cache_read_input_tokens', 0)
                    st['cache_create'] += usage.get('cache_creation_input_tokens', 0)
                    st['assistant_msgs'] += 1
                    model = e['message'].get('model', 'unknown')
                    model_counts[model] = model_counts.get(model, 0) + 1
                    for content in e['message'].get('content', []):
                        ct = content.get('type', '')
                        if ct == 'tool_use':
                            st['tool_calls'] += 1
                            tn = content.get('name', '?')
                            st['tool_names'][tn] = st['tool_names'].get(tn, 0) + 1
                            tool_counts[tn] = tool_counts.get(tn, 0) + 1
                        elif ct == 'thinking':
                            st['thinking_tokens'] += len(content.get('thinking', '')) // 4
            elif t == 'user' and 'message' in e:
                st['user_msgs'] += 1

    total_input += st['input']
    total_output += st['output']
    total_cache_read += st['cache_read']
    total_cache_create += st['cache_create']
    if st['input'] > 0:
        st['file'] = os.path.basename(f)
        st['ratio'] = st['output'] / st['input']
        session_stats.append(st)

session_stats.sort(key=lambda x: x['input'], reverse=True)

print(f'Sessions with tokens: {len(session_stats)}')
print(f'Total input:  {total_input:>15,}')
print(f'Total output: {total_output:>15,}')
print(f'Total cache_r:{total_cache_read:>15,}')
print(f'Total cache_c:{total_cache_create:>15,}')
print(f'Grand total:  {(total_input+total_output+total_cache_read+total_cache_create):>15,}')
print()

# Top tool usage
print('=== TOP TOOLS ===')
for tn, tc in sorted(tool_counts.items(), key=lambda x: -x[1])[:20]:
    print(f'  {tc:>5d}  {tn}')
print()

# Model distribution
print('=== MODELS ===')
for m, c in sorted(model_counts.items(), key=lambda x: -x[1]):
    print(f'  {c:>5d}  {m}')
print()

# Top sessions
print('=== TOP 5 SESSIONS (by input) ===')
for s in session_stats[:5]:
    print(f"  {s['file']}")
    print(f"    in={s['input']:,} out={s['output']:,} cache_r={s['cache_read']:,} cache_c={s['cache_create']:,}")
    print(f"    tools={s['tool_calls']} ratio={s['ratio']:.4f} (1 out per {1/max(s['ratio'],0.0001):.0f} in)")
print()

# Worst ratios
print('=== WORST RATIOS (most input per output output) ===')
worst = sorted(session_stats, key=lambda x: x['ratio'])
for s in worst[:5]:
    print(f"  {s['file']}: 1 out per {1/max(s['ratio'],0.0001):.0f} in (in={s['input']:,} out={s['output']:,})")
print()

# Efficiency stats
ratios = [s['ratio'] for s in session_stats]
avg_ratio = sum(ratios) / len(ratios) if ratios else 0
print(f'Avg output/input ratio: {avg_ratio:.4f}')
print(f'Median output/input ratio: {sorted(ratios)[len(ratios)//2]:.4f}' if ratios else '')

# Cache efficiency
cache_hit_rate = total_cache_read / max(total_input, 1)
print(f'Cache hit rate: {cache_hit_rate:.2%}')
print(f'Cache write rate: {total_cache_create / max(total_input, 1):.2%}')
