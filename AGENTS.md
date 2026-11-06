# Regras operacionais

- Trabalhe em incrementos pequenos e no escopo autorizado. Não implemente a visão futura sem nova autorização.
- Priorize funcionalidade, velocidade e economia de recursos. Use recursos nativos do Codex antes de criar alternativas e ferramentas determinísticas quando IA for desnecessária.
- Use Sol para coordenação e decisões complexas; use Luna em subtarefas delimitadas quando apropriado. Delegue somente com benefício concreto, nunca para demonstrar delegação.
- Considere dois filhos simultâneos como limite inicial ajustável, sujeito à configuração efetiva do ambiente. Não altere configurações globais sem autorização explícita.
- Respeite as políticas de segurança, sandbox e autorização existentes. Não registre credenciais, caminhos privados ou detalhes sensíveis em arquivos públicos.
- Mantenha o projeto independente do TARS e preserve possibilidades de reutilização futura sem introduzir dependências antecipadas.
- Agentes especializados, skills, scripts, serviços, dashboards e outras infraestruturas exigem escopo próprio.
- Revise o diff e faça apenas verificações proporcionais à mudança. Encerre quando a entrega estiver funcional, sem auditorias extensas ou documentação excessiva.
- Não realize push, merge ou publicação sem autorização explícita.
- Sol decide e distribui; não relê toda a implementação por padrão. Tarefas simples dispensam delegação.
- Developer recebe escopo delimitado; cada especialista lê somente o contexto necessário à tarefa. Reviewer atua conforme risco, sem repetição; dispense-o em documentação trivial.
- Prefira determinismo antes de inferência; use evidências resumidas em vez de logs completos. O coordenador consulta detalhes somente diante de erro, inconsistência ou decisão relevante.
- Paralelize somente trabalho independente.

## Retorno dos especialistas

Prefira 5–8 linhas, em texto livre, sem JSON obrigatório:

- Estado: COMPLETED / BLOCKED / NEEDS_FIX.
- Entrega: resultado realizado.
- Arquivos: caminhos afetados, quando houver.
- Verificações: o que foi executado e resultado, com evidência técnica suficiente.
- Problemas e próxima ação: quando necessários.

Detalhes adicionais sob demanda; preserve sempre informações indispensáveis.
