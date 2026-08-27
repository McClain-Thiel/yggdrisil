---
hide:
  - navigation
  - toc
---

# Yggdrisil { .home-document-title }

<div class="hero-grid">
  <div class="hero-copy">
    <span class="hero-kicker">Persistent search infrastructure</span>
    <h1>Agentic optimization, recorded as a graph.</h1>
    <p>Yggdrisil runs policies over user-defined states, deduplicates convergent
    trajectories, and keeps transitions, evaluator evidence, and policy
    decisions in a durable DAG.</p>
    <div class="hero-actions">
      <a href="getting-started/" class="md-button md-button--primary">Get started</a>
      <a href="architecture/" class="md-button">Read the architecture</a>
    </div>
  </div>
  <div class="hero-install">
    <div class="install-label"><span>Install</span><span>Python 3.11+</span></div>
    <code><span>$</span> pip install git+https://github.com/McClain-Thiel/yggdrisil.git</code>
    <p>Core runtime + SQLite graph + local DAG inspector.</p>
    <a href="api/">Browse the Python API <span aria-hidden="true">→</span></a>
  </div>
</div>

<div class="boundary-map" aria-label="Yggdrisil runtime boundaries">
  <div class="boundary-node">
    <span>Application</span>
    <strong>Problem</strong>
    <small>state + action semantics</small>
  </div>
  <div class="boundary-node">
    <span>Application</span>
    <strong>Policy</strong>
    <small>decisions + proposals</small>
  </div>
  <div class="boundary-node boundary-node--core">
    <span>Runtime</span>
    <strong>Runner</strong>
    <small>validate + apply</small>
  </div>
  <div class="boundary-node">
    <span>Storage</span>
    <strong>StateGraph</strong>
    <small>DAG + evidence</small>
  </div>
</div>

<p class="boundary-caption">
  You own the domain and the policy. Yggdrisil owns mutation, persistence,
  limits, and resume.
</p>

<div class="framework-grid">
  <article class="framework-card">
    <span class="card-label">Graph</span>
    <h2>Merge convergent paths</h2>
    <p>The same logical state is stored once. Trajectories that collide on
    <a href="api/#yggdrisil.problem.Problem"><code>state_key</code></a> become
    one node with multiple parents.</p>
  </article>
  <article class="framework-card">
    <span class="card-label">Policy</span>
    <h2>Swap the search strategy</h2>
    <p>Use random search, best-first expansion, tool-using agents, or your own
    policy against the same graph and resource limits.</p>
  </article>
  <article class="framework-card">
    <span class="card-label">Runtime</span>
    <h2>Keep writes in one place</h2>
    <p>Policies query a read-only graph and return proposals. The runner
    validates and commits transitions atomically.</p>
  </article>
</div>

<div class="example-band">
  <div>
    <span class="card-label">Worked example</span>
    <h2>Make 24 with inspectable decisions</h2>
    <p>The tutorial composes four numbers with arithmetic tools. Each accepted
    policy call becomes a decision linked to its proposed transitions, so the
    graph—not a chat log—is the experiment record.</p>
    <a href="tutorial/" class="md-button md-button--primary">Build the example</a>
  </div>
  <div class="puzzle" aria-label="Make 24 number pool">
    <div><span>1</span><span>3</span><span>4</span><span>6</span></div>
    <strong>→ 24</strong>
  </div>
</div>
