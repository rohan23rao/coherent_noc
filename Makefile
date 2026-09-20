# Repo-root forwarder. The real build lives in sim/Makefile.
.PHONY: all lint test waves mutate clean help
all lint test waves mutate clean help:
	@$(MAKE) --no-print-directory -C sim $@ $(if $(TEST),TEST=$(TEST),)
