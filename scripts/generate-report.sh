#!/usr/bin/env bash

set -euo pipefail

# ============================================================
# Configuration
# ============================================================

OUTPUT_DIR="docs"
OUTPUT_MD="$OUTPUT_DIR/final-report.md"
OUTPUT_PDF="$OUTPUT_DIR/gabriel_aguiar_aquino-crypto-intelligence-agent.pdf"

REPORT_TITLE="Crypto Intelligence Agent"
REPORT_SUBTITLE="Relatório técnico das Fases 1 a 5"
REPORT_AUTHOR="Gabriel Aguiar Aquino"

# Relative links in the chapters (../app/..., ../docs/...) point to files in the repository.
# In the PDF they become links to the same files on GitHub.
REPO_URL="https://github.com/gabriel-q7/crypto-intelligent-agent/blob/main"

# Paths are relative to the repository root. Their order is preserved.
REPORT_SOURCES=(
    "Report/relatorio-final/00-repositorio.md"
    "Report/relatorio-final/01-uso-de-ia.md"
    "Report/relatorio-final/02-introducao.md"
    "Report/relatorio-final/03-arquitetura.md"

    "Report/fase-01-chain-estruturada.md"
    "Report/fase-02-agente-ferramentas.md"
    "Report/fase-03-memoria-conversacional.md"
    "Report/fase-04-ferramentas-rag.md"
    "Report/fase-05-agente-integrado.md"

    "Report/relatorio-final/09-decisoes-transversais.md"
    "Report/relatorio-final/10-conclusao.md"
    "Report/relatorio-final/99-referencias.md"
)

# Checks every number quoted in the chapters against the evidence files.
NUMBER_VERIFIER="scripts/verify_report_numbers.py"

# ============================================================
# Implementation
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR_ABS="$REPO_ROOT/$OUTPUT_DIR"
OUTPUT_MD_ABS="$REPO_ROOT/$OUTPUT_MD"
OUTPUT_PDF_ABS="$REPO_ROOT/$OUTPUT_PDF"

info() {
    printf '[INFO] %s\n' "$*"
}

warn() {
    printf '[WARNING] %s\n' "$*" >&2
}

error() {
    printf '[ERROR] %s\n' "$*" >&2
}

display_path() {
    local path="$1"
    if [[ "$path" == "$REPO_ROOT/"* ]]; then
        printf '%s' "${path#"$REPO_ROOT/"}"
    else
        printf '%s' "$path"
    fi
}

yaml_escape() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s' "$value"
}

# Report date in Brazilian Portuguese, independent of the system locale.
report_date() {
    local months=(janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro)
    local day month year
    day="$(date +%-d)"
    month="$(date +%-m)"
    year="$(date +%Y)"
    printf '%s de %s de %s' "$day" "${months[$((month - 1))]}" "$year"
}

validate_dependencies() {
    local dependency
    local missing=0

    info "Validating dependencies..."
    for dependency in bash pandoc xelatex python3 awk sed; do
        if command -v "$dependency" >/dev/null 2>&1; then
            info "Dependency available: $dependency"
        else
            error "Required dependency not found: $dependency"
            missing=1
        fi
    done

    if ((missing)); then
        error "Install the missing tool(s) and run the script again. No packages were installed automatically."
        return 1
    fi
}

verify_numbers() {
    info "Verifying report numbers against the evidence files..."
    if ! python3 "$REPO_ROOT/$NUMBER_VERIFIER"; then
        error "Some numbers in the chapters do not match the evidence files. No report was generated."
        return 1
    fi
}

validate_sources() {
    local source
    local source_abs
    local missing=0

    info "Validating Markdown sources..."
    for source in "${REPORT_SOURCES[@]}"; do
        source_abs="$REPO_ROOT/$source"
        if [[ ! -f "$source_abs" ]]; then
            error "Markdown source not found:"
            printf '%s\n' "$source" >&2
            missing=1
        fi
    done

    if ((missing)); then
        error "Source validation failed. No report was generated."
        return 1
    fi
}

# Relative links must point to files that exist; a broken link would become a dead GitHub URL.
validate_links() {
    local source
    local source_abs
    local source_dir
    local match
    local target

    info "Checking relative links..."
    for source in "${REPORT_SOURCES[@]}"; do
        source_abs="$REPO_ROOT/$source"
        source_dir="$(dirname "$source_abs")"
        while IFS= read -r match; do
            [[ -z "$match" ]] && continue
            target="${match#*](}"
            target="${target%)}"
            target="${target%%#*}"
            case "$target" in
                http://*|https://*|mailto:*|'') continue ;;
            esac
            [[ -e "$source_dir/$target" ]] || warn "Link in $(display_path "$source_abs") points to a missing file: $target"
        done < <(grep -oE '\]\([^)]+\)' "$source_abs" || true)
    done
}

# Per-source transformations before concatenation:
# - manual heading numbers ("## 5.2 Título") are removed: pandoc numbers the sections;
# - relative links become GitHub links (resolved against the source's directory);
# - fences without a language become "text", so the long lines can wrap in the PDF.
transform_source() {
    local source_dir_rel="$1"
    # Strikeout (~~...~~) needs soul.sty, missing from minimal TeX installs; the struck items in
    # the diaries already say "feito"/"resolvido", so the markers are dropped. Two symbols have no
    # glyph in the DejaVu fonts and are replaced by text.
    sed -E -e 's/^(#{1,6}) [0-9]+(\.[0-9]+)*\.? +/\1 /' -e 's/~~//g' -e 's/∥/||/g' -e 's/✅/(ok)/g' |
        awk -v base="$source_dir_rel" -v url="$REPO_URL" '
            function resolve(path,    parts, n, i, out, k) {
                n = split(base "/" path, parts, "/")
                k = 0
                for (i = 1; i <= n; i++) {
                    if (parts[i] == "" || parts[i] == ".") continue
                    if (parts[i] == "..") { if (k > 0) k--; continue }
                    out[++k] = parts[i]
                }
                path = ""
                for (i = 1; i <= k; i++) path = path (i > 1 ? "/" : "") out[i]
                return path
            }
            /^```/ {
                if (!in_code && $0 == "```") { print "```text"; in_code = 1; next }
                in_code = !in_code
                print
                next
            }
            in_code { print; next }
            {
                line = $0
                result = ""
                while (match(line, /\]\([^)]+\)/)) {
                    target = substr(line, RSTART + 2, RLENGTH - 3)
                    if (target !~ /^(https?:|mailto:|#)/) target = url "/" resolve(target)
                    result = result substr(line, 1, RSTART - 1) "](" target ")"
                    line = substr(line, RSTART + RLENGTH)
                }
                print result line
            }' |
        # Tables with 8 or more columns (the Phase 1 benchmark has 13) do not fit the page at the
        # body font size; they are wrapped in a smaller font.
        awk '
            { lines[NR] = $0 }
            END {
                i = 1
                while (i <= NR) {
                    if (lines[i] ~ /^\|/ && i < NR && lines[i + 1] ~ /^\|[ :|-]+\|[ ]*$/) {
                        separator = lines[i + 1]
                        columns = gsub(/\|/, "|", separator) - 1
                        if (columns >= 8) print "\n\\begingroup\\scriptsize\n"
                        while (i <= NR && lines[i] ~ /^\|/) print lines[i++]
                        if (columns >= 8) print "\n\\endgroup\n"
                        continue
                    }
                    print lines[i++]
                }
            }'
}

generate_markdown() {
    local source
    local source_abs
    local temp_md

    info "Generating consolidated Markdown..."
    mkdir -p "$OUTPUT_DIR_ABS"
    temp_md="$(mktemp "$OUTPUT_DIR_ABS/.final-report.md.XXXXXX")"

    {
        printf '%s\n' '---'
        printf 'title: "%s"\n' "$(yaml_escape "$REPORT_TITLE")"
        printf 'subtitle: "%s"\n' "$(yaml_escape "$REPORT_SUBTITLE")"
        printf 'author: "%s"\n' "$(yaml_escape "$REPORT_AUTHOR")"
        printf 'date: "%s"\n' "$(yaml_escape "$(report_date)")"
        printf '%s\n' 'lang: pt-BR' 'geometry: margin=2.2cm' 'toc: true' 'numbersections: true'
        printf '%s\n' 'header-includes:' \
            '  - \usepackage{float}' \
            '  - \floatplacement{figure}{H}' \
            '  - \usepackage{fvextra}' \
            '  - \DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,commandchars=\\\{\},fontsize=\scriptsize}' \
            '---' '' '\newpage'

        for source in "${REPORT_SOURCES[@]}"; do
            source_abs="$REPO_ROOT/$source"
            info "Adding: $source" >&2
            printf '\n\n<!-- Source: %s -->\n\n' "$source"
            transform_source "$(dirname "$source")" <"$source_abs"
            printf '\n\n\\newpage\n'
        done
    } >"$temp_md"

    mv "$temp_md" "$OUTPUT_MD_ABS"
}

generate_pdf() {
    local temp_pdf

    info "Generating PDF..."
    temp_pdf="$OUTPUT_DIR_ABS/.final-report.$$.pdf"
    command rm -f "$OUTPUT_PDF_ABS"

    if ! (
        cd "$REPO_ROOT"
        pandoc "$OUTPUT_MD_ABS" \
            --from=markdown+pipe_tables+fenced_code_blocks+lists_without_preceding_blankline \
            --pdf-engine=xelatex \
            --resource-path="$REPO_ROOT" \
            --toc \
            --number-sections \
            --syntax-highlighting=tango \
            --variable=papersize:a4 \
            --variable=fontsize:11pt \
            --variable=babel-lang:portuguese-brazil \
            --variable=mainfont:"DejaVu Serif" \
            --variable=sansfont:"DejaVu Sans" \
            --variable=monofont:"DejaVu Sans Mono" \
            --variable=colorlinks:true \
            --variable=linkcolor:blue \
            --variable=urlcolor:blue \
            --output="$temp_pdf"
    ); then
        command rm -f "$temp_pdf"
        error "PDF generation failed."
        error "The Markdown report was generated successfully:"
        printf '%s\n' "$OUTPUT_MD" >&2
        return 1
    fi

    mv "$temp_pdf" "$OUTPUT_PDF_ABS"
}

main() {
    info "Repository root: $REPO_ROOT"
    validate_dependencies
    validate_sources
    verify_numbers
    validate_links
    generate_markdown
    generate_pdf

    info "Report generated successfully."
    printf '\nMarkdown:\n  %s\n\nPDF:\n  %s\n' "$OUTPUT_MD" "$OUTPUT_PDF"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
