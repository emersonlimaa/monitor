# Monitor de Recursos

Aplicativo de mesa para Linux que mostra o que está acontecendo no computador,
o que está ocupando o disco e desinstala programas sem terminal.

Feito em Python com GTK 4 e libadwaita: usa as bibliotecas que o sistema já
tem, então o pacote instalado ocupa menos de 1 MB e a janela abre como
aplicativo nativo do GNOME.

## O que ele faz

**Recursos** — processador (uso total, por núcleo, frequência, temperatura),
memória e swap, cada disco com espaço livre, rede, tempo ligado e os
programas que mais consomem no momento, com opção de encerrar.

**Componentes** — todas as peças do computador: modelo da placa-mãe e versão
da BIOS, processador com núcleos, frequências e cache, placas de vídeo (com
memória e temperatura nas NVIDIA), cada disco com modelo e firmware, os
monitores com nome, polegadas e resolução nativa lidos do EDID, placas de
rede, placas de som e os aparelhos USB conectados. Um botão copia a lista
inteira, útil para pedir suporte ou anunciar a máquina. Os pentes de memória
(slot, velocidade, fabricante) só aparecem com senha, porque é a única parte
que exige administrador.

**Aplicativos** — tudo que está instalado por apt, Snap e Flatpak, com nome
de verdade, ícone e quanto cada um ocupa. Um clique desinstala. Antes de
remover, o app pergunta ao sistema o que sairia junto e mostra em português:
"libera cerca de 340 MB, saem também 4 dependências sem uso".

**Disco** — escolha uma pasta ou um disco inteiro e veja a porcentagem por
tipo de conteúdo: vídeos, jogos, caches e builds, projetos de programação,
documentos, música, imagens, máquinas virtuais, pacotes, dados de
aplicativos. Serve para responder "onde foram parar meus 700 GB".

**Limpeza** — lixeira, miniaturas, caches de aplicativos, instaladores já
usados do apt, dependências órfãs, versões antigas de snaps, relatórios de
falha e registros do sistema. Cada item mostra quanto libera, e nada que
seja arquivo seu entra na lista. Tem também o TRIM do SSD.

## Instalação

```bash
sudo apt install ./monitor-recursos_1.0.0_all.deb
```

Depois é só procurar por "Monitor de Recursos" no menu de aplicativos.

Para remover: `sudo apt remove monitor-recursos`.

## Rodar direto do código, sem instalar

```bash
./monitor-recursos
```

Precisa de `python3-gi`, `gir1.2-gtk-4.0` e `gir1.2-adw-1` instalados
(já vêm no Ubuntu com GNOME).

## Gerar o pacote .deb

```bash
./scripts/build-deb.sh
```

O arquivo sai em `dist/`. Não precisa de root.

## Como a desinstalação funciona por baixo

A interface nunca monta nem mostra linha de comando. Ela pede uma operação
pelo nome a um auxiliar privilegiado (`/usr/libexec/monitor-recursos/`), que
roda via `pkexec` com uma ação do polkit própria — por isso a janela de senha
diz "É preciso autenticar para desinstalar programas e liberar espaço", e não
o nome de um comando.

Esse auxiliar traduz a saída do `apt` para frases ("Removendo Slack…") e
**revalida tudo por conta própria**, sem confiar na interface:

- nomes de pacote passam por expressão regular antes de qualquer execução;
- pacotes essenciais, de prioridade `required`/`important` e uma lista de
  padrões protegidos (`systemd*`, `linux-image*`, `gnome-shell*`, drivers,
  o próprio Python e GTK, entre outros) são recusados;
- a remoção é simulada antes de valer: se a simulação mostrar que algum
  item protegido sairia junto, a operação é cancelada inteira;
- a limpeza de pastas do sistema só aceita caminhos dentro de uma lista
  branca (`/var/cache`, `/var/crash`, …), e a limpeza de caches do usuário
  roda sem privilégio nenhum, só dentro da pasta pessoal.

## Como a análise de disco funciona

Os tamanhos são o espaço **realmente alocado** (blocos), igual ao `du`:
arquivos esparsos — imagens de emulador, discos de VM — declaram muito mais
do que ocupam, e contá-los pelo tamanho aparente faria uma pasta pessoal de
671 GB parecer 1,6 TB.

Ler metadados de milhões de arquivos é limitado pela latência do disco, não
pela CPU: uma thread só mantém o SSD em fila 1 e rende cerca de 6 mil
arquivos por segundo. A varredura então abre a raiz até formar uma frente de
subárvores independentes e percorre várias em paralelo, cada uma de forma
serial — a pasta pessoal deste autor (2,6 milhões de arquivos) sai em pouco
mais de dois minutos na primeira vez e em segundos nas seguintes, porque o
cache do sistema já está quente.

A classificação olha a extensão do arquivo e o contexto da pasta: uma pasta
com `.git`, `package.json`, `Cargo.toml` e afins vira "projeto de
programação"; `node_modules`, `.venv`, `build`, `.gradle` e companhia viram
"cache e build"; `steamapps`, `.wine`, `lutris` viram "jogos".

## De onde vêm as informações de hardware

Tudo de `/sys` e `/proc`, sem `lspci`, `lsusb` nem senha. Os códigos numéricos
de fabricante e produto são traduzidos pelas bases que o próprio sistema já
traz (`pci.ids`, `usb.ids`, `pnp.ids`), e o nome e o tamanho dos monitores
saem de um leitor de EDID escrito no projeto. A coleta inteira leva cerca de
40 milissegundos.

A exceção é a memória: nenhum arquivo do kernel descreve os pentes, só o
`dmidecode` lendo a tabela SMBIOS, que exige administrador. Por isso essa
parte fica atrás de um botão e passa pelo mesmo auxiliar privilegiado.

## Organização do código

```
src/monitor_recursos/
├── core/        regras e coleta, sem nada de interface
│   ├── sysinfo.py     /proc e /sys: CPU, memória, discos, rede, processos
│   ├── hardware.py    inventário das peças, pci.ids/usb.ids/pnp.ids e EDID
│   ├── scanner.py     varredura paralela e classificação por tipo
│   ├── packages.py    inventário apt/Snap/Flatpak e simulação de remoção
│   ├── cleanup.py     itens recuperáveis e apagamento seguro
│   └── privileged.py  conversa com o auxiliar via pkexec
├── ui/          telas GTK; widgets desenhados com Gsk (sem cairo)
└── app.py       janela principal
helper/monitor-recursos-helper   o que roda como administrador
```

## Licença

MIT.
