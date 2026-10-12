class PrismisDaemon < Formula
  desc "Prismis daemon: fetches content, scores it against your interests, serves the API"
  homepage "https://github.com/nickpending/prismis"
  url "https://github.com/nickpending/prismis/archive/refs/tags/v0.0.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  version "0.0.0"
  license "MIT"
  head "https://github.com/nickpending/prismis.git", branch: "main"

  depends_on "python@3.14"
  depends_on "uv"

  # The wheels' compiled modules (jiter, torch, ...) ship @rpath install names and no
  # header room to rewrite them; brew's relocation step fails on them without this.
  preserve_rpath

  def install
    ENV["UV_CACHE_DIR"] = buildpath/".uv-cache"
    ENV["UV_PYTHON_DOWNLOADS"] = "never"

    python = Formula["python@3.14"].opt_bin/"python3.14"
    system "uv", "venv", libexec, "--python", python

    # The same pinning `make install-daemon` uses: the lock's exact versions as
    # constraints. torch, tokenizers and pydantic-core cannot build from source
    # under brew's no-binary pip path, so uv installs their wheels.
    lock = Utils.safe_popen_read("uv", "export", "--frozen", "--no-dev", "--no-emit-project",
                                 "--no-hashes", "--no-header", "--project", "daemon")
    pins = lock.lines.grep(/\A[A-Za-z0-9_.-]+(\[[^\]]*\])?==/)
    (buildpath/"constraints.txt").write(pins.join)

    system "uv", "pip", "install", "--python", libexec/"bin/python",
           "--constraints", buildpath/"constraints.txt", buildpath/"daemon"

    bin.install_symlink libexec/"bin/prismis-daemon"
  end

  service do
    run [opt_bin/"prismis-daemon"]
    keep_alive true
    log_path var/"log/prismis-daemon.log"
    error_log_path var/"log/prismis-daemon.log"
  end

  def caveats
    <<~EOS
      First run:

        1. Put your LLM key in ~/.config/prismis/.env:
             OPENROUTER_API_KEY=sk-your-key-here
        2. Run `prismis-daemon` once to write ~/.config/prismis/config.toml, then
           tell Prismis what you care about:
             prismis-cli context bootstrap
        3. Check that config, LLM services and sources are wired up:
             prismis-daemon verify
        4. Start the daemon at login and keep it running:
             brew services start prismis-daemon
    EOS
  end

  test do
    system bin/"prismis-daemon", "--help"
  end
end
