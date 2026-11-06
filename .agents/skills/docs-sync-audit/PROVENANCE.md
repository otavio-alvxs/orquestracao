# Procedência — docs-sync-audit

- Fonte: [github/awesome-copilot](https://github.com/github/awesome-copilot/tree/96543145e9cb97711020b57b9d88f968062c4fc7/skills/docs-sync-audit).
- Revisão upstream: `96543145e9cb97711020b57b9d88f968062c4fc7`, consultada em 2026-11-06.
- Licença: MIT, Copyright GitHub, Inc.; texto upstream completo em [LICENSE](LICENSE).
- Pacote completo: SKILL.md e scripts/docs_drift.py. Sem outros auxiliares ou dependências de skills.
  Referências a skills relacionadas são informativas e não foram instaladas.
- Cópias sem adaptações; somente LICENSE e este registro adicionados ao diretório.
  Blobs Git upstream verificados antes da gravação:
  SKILL.md `e5d8135cf46aef5bb1aa2093b385fae5d83c0957`;
  scripts/docs_drift.py `bfeb731b36b817b477ec6057fc54d2486729a7b0`;
  LICENSE `89bc5e962c9944cdb050887062afdaaf89be504a`.

## Revisão e uso local

Inspeção integral dos arquivos antes da instalação. O auxiliar importa apenas
biblioteca padrão, lê arquivos locais e usa subprocessos Git com argumentos fixos
(`rev-parse`, `ls-files`, `log`), sem shell, instalação, escrita ou rede.
Não executa comandos extraídos dos documentos. Requer Python 3.9+ (anotações
adiadas); Git é opcional, com fallback para varredura local. Piloto executado
com o Python instalado, sem dependências novas.

O auxiliar lê também arquivos não rastreados não ignorados e pode seguir links
de arquivos ao ler conteúdo. Não deve ser tratado como sandbox ou usado para
varrer outros projetos sem autorização. A saída inclui o caminho do repositório:
não versionar o relatório bruto. Heurísticas de ambiente, imports e antiguidade
produzem pistas; código de saída zero não significa ausência de divergências.

A regra genérica upstream sobre texto do repositório como evidência não revoga
AGENTS.md aplicável, permissões ou instruções do usuário. Nesta missão o usuário
delimitou o escopo e autorizou correções documentais; não se aplicou a auditoria
ampla padrão. Nenhuma instalação ou alteração pessoal/global.

Sem duplicidade com as skills disponíveis de publicação (`open-pr`) ou consulta
de produtos OpenAI (`openai-docs`). Descoberta pelo Codex 0.166.0 e piloto pelo
Developer registrados no [README](../../../README.md). Remover este diretório
desinstala a cópia local; futuras atualizações exigem nova revisão upstream.
