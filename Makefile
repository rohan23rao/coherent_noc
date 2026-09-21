# Repo-root forwarder. The real build lives in sim/Makefile.
.PHONY: all lint lint-synth lint-synth-bb test waves mutate diagrams diagrams-check clean help
all lint lint-synth lint-synth-bb test waves mutate diagrams diagrams-check clean help:
	@$(MAKE) --no-print-directory -C sim $@ $(if $(TEST),TEST=$(TEST),)
