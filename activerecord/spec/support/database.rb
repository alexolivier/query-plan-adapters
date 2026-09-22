# frozen_string_literal: true

# The one database every suite in this process uses.
#
# The store is chosen with ADAPTER_TEST_DB, exactly as the drizzle, prisma and sequel harnesses
# choose theirs: `sqlite` (the default, in memory), `postgres` or `mysql`. An unknown value fails
# rather than falling back, because a typo that quietly ran SQLite would report a store as
# covered that nothing executed.
#
# Only the adversarial harness runs on the real servers. Collation, LIKE escaping, cast targets
# and literal typing are translator behaviour, so a store the harness does not execute is a
# store the adapter does not cover. The two offline suites — the translator unit test and the
# contract suite — record and assert SQLite's rendering, and refuse to run anywhere else (see
# Database.require_sqlite!).
module Database
  STORES = %w[sqlite postgres mysql].freeze

  STORE = ENV.fetch("ADAPTER_TEST_DB", "sqlite")
  unless STORES.include?(STORE)
    raise "ADAPTER_TEST_DB=#{STORE.inspect} is not one of #{STORES.join(", ")}"
  end

  # MySQL's default collation makes `=` itself case- and accent-insensitive, and CEL's string
  # equality is byte-exact. That is a store misconfiguration and not an adapter limitation, so
  # the leg pins a binary collation on the tables (see adversarial_models.rb) AND on the
  # connection: the literals the adapter writes — the 'true' and 'false' of string() over a
  # boolean — compare in the collation of the connection, not of a column. The README states the
  # same requirement for a consumer.
  MYSQL_COLLATION = "utf8mb4_0900_bin"

  module_function

  # The connection string of the real server. scripts/test.sh sets it for the compose service
  # it starts; a local run against a server of your own sets it by hand.
  def url
    ENV.fetch("DATABASE_URL") {
      raise "ADAPTER_TEST_DB=#{STORE} needs DATABASE_URL — run it through scripts/test.sh"
    }
  end

  def mysql? = STORE == "mysql"

  def establish!
    return if @established
    @established = true

    case STORE
    when "sqlite"
      ActiveRecord::Base.establish_connection(
        adapter: "sqlite3",
        database: ":memory:",
        # Only one connection. Thus the PRAGMA below applies to each query of the suite, and
        # the tables in memory are the same tables for every query.
        pool: 1
      )
      # CEL compares strings with attention to the case of the letters. The LIKE operator of
      # SQLite does not do this with its default configuration. Without this PRAGMA, the test
      # `contains("a_b")` would also find `xA_by`. Then the rows in the corpus for the collation
      # would agree for an incorrect reason.
      ActiveRecord::Base.connection.execute("PRAGMA case_sensitive_like = ON")
    when "postgres"
      ActiveRecord::Base.establish_connection(url: url, pool: 1)
    when "mysql"
      # `collation_connection` through `variables`, and never an `encoding:` key: that makes
      # ActiveRecord send `SET NAMES utf8mb4 COLLATE ...`, and with that statement the trilogy
      # driver (2.13, against MySQL 8.4) crashes the Ruby process with a segfault on the next
      # query. The connection already speaks utf8mb4, so only the collation needs setting.
      ActiveRecord::Base.establish_connection(
        url: url, pool: 1, variables: {collation_connection: MYSQL_COLLATION}
      )
    end
  end

  # The options every table in the harness is created with.
  def table_options
    mysql? ? {charset: "utf8mb4", collation: MYSQL_COLLATION} : {}
  end

  # The column type of an IEEE-754 double. `t.float` is one on SQLite (REAL) and PostgreSQL
  # (`float`, which is `double precision`), but MySQL's `float` is SINGLE precision: the corpus's
  # doubles would be stored rounded, and a comparison with the PDP's double would disagree.
  def double_type
    mysql? ? "double" : :float
  end

  # The offline suites pin SQLite's rendering byte for byte, and the contract suite reads
  # SQLite's quoting in its patterns. On another store they would fail for a reason that says
  # nothing about the adapter, so they refuse to start instead.
  def require_sqlite!(suite)
    return if STORE == "sqlite"

    raise "#{suite} records and asserts SQLite's rendering and runs on SQLite only; " \
          "ADAPTER_TEST_DB=#{STORE} is for spec/adversarial_conformance_spec.rb"
  end
end
