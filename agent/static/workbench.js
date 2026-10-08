const thread = document.getElementById('thread');
    const emptyBox = document.getElementById('empty');
    const mainEl = document.getElementById('main');
    const input = document.getElementById('message');
    const sendBtn = document.getElementById('send');
    const modeSel = document.getElementById('mode');
    const providerSel = document.getElementById('provider');
    const sessionEl = document.getElementById('session');
    const fileEl = document.getElementById('file');
    const filebar = document.getElementById('filebar');

    const MODE_LABEL = {
      review: '代码审查', explain: '代码解释', generate: '代码生成',
      test: '测试生成', refactor: '重构建议', ask: '通用问答'
    };
    const UPLOAD_PROMPTS = {
      review: '审查 {path}', explain: '解释 {path}', test: '为 {path} 生成单元测试',
      refactor: '重构 {path}', generate: '审查 {path}', ask: '审查 {path}'
    };

    const escapeHtml = (s) => String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    /** 轻量 Markdown 渲染：标题 / 表格 / 代码块 / 列表 / 引用 / 行内样式 */
    function renderMarkdown(src) {
      const lines = String(src || '').split(/\r?\n/);
      const out = [];
      const inline = (s) => escapeHtml(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      const splitRow = (l) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
      const isSpecial = (l) => /^```/.test(l.trim()) || /^\s*\|/.test(l) || /^#{1,4}\s/.test(l)
        || /^\s*>/.test(l) || /^\s*([-*+]|\d+\.)\s+/.test(l) || /^\s*(-{3,}|\*{3,})\s*$/.test(l);

      let i = 0;
      while (i < lines.length) {
        const line = lines[i];

        if (/^```/.test(line.trim())) {                       // 代码块
          const lang = line.trim().slice(3).trim();
          const buf = [];
          i += 1;
          while (i < lines.length && !/^```/.test(lines[i].trim())) { buf.push(lines[i]); i += 1; }
          i += 1;
          out.push('<pre class="code"' + (lang ? ' data-lang="' + escapeHtml(lang) + '"' : '')
            + '><code>' + escapeHtml(buf.join('\n')) + '</code></pre>');
          continue;
        }

        if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
          const head = splitRow(line);                        // 表格
          i += 2;
          const rows = [];
          while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(splitRow(lines[i])); i += 1; }
          let html = '<table><thead><tr>' + head.map((h) => '<th>' + inline(h) + '</th>').join('')
            + '</tr></thead><tbody>';
          for (const row of rows) {
            html += '<tr>' + row.map((c) => '<td>' + inline(c) + '</td>').join('') + '</tr>';
          }
          out.push(html + '</tbody></table>');
          continue;
        }

        const heading = /^(#{1,4})\s+(.*)$/.exec(line);
        if (heading) {
          const level = Math.min(heading[1].length + 1, 5);
          out.push('<h' + level + '>' + inline(heading[2]) + '</h' + level + '>');
          i += 1;
          continue;
        }

        if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push('<hr>'); i += 1; continue; }

        if (/^\s*>/.test(line)) {                             // 引用
          const buf = [];
          while (i < lines.length && /^\s*>/.test(lines[i])) {
            buf.push(lines[i].replace(/^\s*>\s?/, '')); i += 1;
          }
          out.push('<blockquote>' + buf.map(inline).join('<br>') + '</blockquote>');
          continue;
        }

        if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {              // 列表
          const ordered = /^\s*\d+\./.test(line);
          const buf = [];
          while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
            buf.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, '')); i += 1;
          }
          const tag = ordered ? 'ol' : 'ul';
          out.push('<' + tag + '>' + buf.map((t) => '<li>' + inline(t) + '</li>').join('') + '</' + tag + '>');
          continue;
        }

        if (!line.trim()) { i += 1; continue; }

        const buf = [lines[i++]];
        while (i < lines.length && lines[i].trim() && !isSpecial(lines[i])) { buf.push(lines[i]); i += 1; }
        out.push('<p>' + buf.map(inline).join('<br>') + '</p>');
      }
      return out.join('\n');
    }

    function scrollDown() { mainEl.scrollTop = mainEl.scrollHeight; }
    function hideEmpty() { if (emptyBox && emptyBox.parentNode) emptyBox.parentNode.removeChild(emptyBox); }
    function el(tag, cls, text) {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text;
      thread.appendChild(node);
      scrollDown();
      return node;
    }
    function userBubble(text) {
      const wrap = el('div', 'msg user');
      wrap.appendChild(el('div', 'bubble', text));
      return wrap;
    }
    function assistantCard() {
      const wrap = el('div', 'msg assistant');
      wrap.appendChild(el('div', 'avatar', 'AI'));
      const bubble = el('div', 'bubble');
      wrap.appendChild(bubble);
      const meta = el('div', 'meta');
      bubble.appendChild(meta);
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = MODE_LABEL[modeSel.value] || '自动识别';
      meta.appendChild(badge);
      const info = document.createElement('span');
      meta.appendChild(info);
      const steps = document.createElement('div');
      steps.className = 'steps';
      bubble.appendChild(steps);
      const answer = document.createElement('div');
      answer.className = 'answer';
      bubble.appendChild(answer);
      return { wrap, badge, info, steps, answer };
    }
    function stepRow(container, text, ok) {
      const row = document.createElement('div');
      row.className = 'step';
      const tick = document.createElement('span');
      tick.className = 'tick ' + (ok ? 'ok' : 'fail');
      tick.textContent = ok ? '✓' : '✗';
      row.appendChild(tick);
      const body = document.createElement('span');
      body.textContent = text;
      row.appendChild(body);
      container.appendChild(row);
      scrollDown();
    }
    function thinkingRow(container) {
      const row = document.createElement('div');
      row.className = 'thinking';
      row.innerHTML = '思考中<i></i><i></i><i></i>';
      container.appendChild(row);
      scrollDown();
      return row;
    }

    function renderFileChip(file) {
      filebar.innerHTML = '';
      if (!file) return;
      const chip = document.createElement('span');
      chip.className = 'filechip';
      chip.appendChild(document.createTextNode('📎 ' + file.name));
      const clear = document.createElement('button');
      clear.type = 'button';
      clear.textContent = '✕';
      clear.title = '取消选择';
      clear.onclick = () => { fileEl.value = ''; renderFileChip(null); };
      chip.appendChild(clear);
      const hint = document.createElement('span');
      hint.textContent = '将按「' + (MODE_LABEL[modeSel.value] || '自动识别') + '」模式调用';
      hint.style.color = 'var(--dim)';
      chip.appendChild(hint);
      filebar.appendChild(chip);
    }

    const $ = (id) => document.getElementById(id);
    let projectFiles = [];
    let scanResult = null;
    let findingLimit = 60;
    let previewPath = '';
    const labels = {overview: '项目概览', assistant: '代码助手', git: 'Git 改动'};
    function showView(name) {
      document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === 'view-' + name));
      document.querySelectorAll('[data-view]').forEach(node => node.classList.toggle('active', node.dataset.view === name));
      $('view-label').textContent = labels[name];
    }
    async function api(path, payload) {
      const response = await fetch(path, payload === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok || result.ok === false) throw new Error(result.error || ('HTTP ' + response.status));
      return result;
    }
    function notify(message) { $('notice').textContent = message || ''; }
    function renderFiles() {
      const query = $('file-search').value.toLowerCase();
      $('file-list').replaceChildren();
      for (const file of projectFiles.filter(file => file.path.toLowerCase().includes(query))) {
        const button = document.createElement('button');
        button.className = 'file-item';
        button.title = file.path;
        const icon = document.createElement('span'); icon.className = 'file-icon'; icon.textContent = file.language.slice(0, 3);
        const name = document.createElement('span'); name.textContent = file.path;
        button.append(icon, name);
        button.onclick = () => openFile(file.path);
        $('file-list').appendChild(button);
      }
    }
    async function loadProject() {
      try {
        const data = await api('/api/project');
        projectFiles = data.files;
        $('project-name').textContent = data.name;
        $('project-name').title = data.name;
        $('file-count').textContent = data.file_count;
        $('metric-files').textContent = data.source_count;
        $('metric-files-note').textContent = data.file_count + ' 个可浏览文本文件' + (data.truncated ? '，索引已截断' : '');
        renderFiles();
        const area = $('project-languages'); area.replaceChildren();
        for (const [language, count] of Object.entries(data.languages).sort((a,b)=>b[1]-a[1])) {
          const row = document.createElement('div'); row.className = 'language-row';
          const name = document.createElement('span'); name.textContent = language.toUpperCase();
          const value = document.createElement('span'); value.className = 'muted'; value.textContent = count + ' 文件';
          row.append(name, value); area.appendChild(row);
          const bar = document.createElement('div'); bar.className = 'language-bar';
          const fill = document.createElement('i'); fill.style.width = (count / Math.max(1,data.source_count) * 100) + '%';
          bar.appendChild(fill); area.appendChild(bar);
        }
        for (const [name, value] of [['项目说明', data.has_readme ? 'README 已发现' : '未发现 README'], ['测试目录', data.has_tests ? '已发现测试文件' : '未发现测试文件'], ['依赖描述', data.manifests.join(', ') || '未发现']]) {
          const row = document.createElement('div'); row.className = 'info-row';
          const label = document.createElement('span'); label.className='muted'; label.textContent=name;
          const text = document.createElement('span'); text.textContent=value;
          row.append(label,text); area.appendChild(row);
        }
      } catch(error) { notify('读取项目失败：' + error.message); }
    }
    async function openFile(path, line) {
      previewPath = path;
      $('preview-title').textContent = path;
      $('preview-content').textContent = '正在读取…';
      $('preview-note').textContent = '只读预览';
      if (!$('file-dialog').open) $('file-dialog').showModal();
      try {
        const data = await api('/api/file?path=' + encodeURIComponent(path));
        const rows = data.content.split('\n');
        $('preview-content').replaceChildren();
        rows.forEach((text,index) => {
          const row = document.createElement('span'); row.className = 'code-line' + (index + 1 === line ? ' highlight' : '');
          const number = document.createElement('span'); number.className = 'number'; number.textContent = index+1;
          row.append(number,document.createTextNode(text || ' ')); $('preview-content').appendChild(row);
        });
        $('preview-note').textContent = rows.length + ' 行 · UTF-8 · 只读预览';
        const highlight = $('preview-content').querySelector('.highlight');
        if (highlight) highlight.scrollIntoView({block:'center'});
      } catch(error) { $('preview-content').textContent = error.message; }
    }
    function askForFile(mode) {
      $('file-dialog').close(); showView('assistant'); modeSel.value = mode;
      input.value = (mode === 'explain' ? '解释 ' : '审查 ') + previewPath;
      autoGrow(); input.focus();
    }
    function renderFindings() {
      const area = $('findings'); area.replaceChildren();
      if (!scanResult) return;
      const severity = $('severity-filter').value;
      const rows = scanResult.findings.filter(item => severity === 'all' || item.severity === severity);
      if (!rows.length) {
        const empty = document.createElement('div'); empty.className='empty-results';
        empty.textContent = scanResult.files_scanned ? '当前筛选下没有规则问题。仍建议运行测试并人工检查。' : '没有可扫描的源文件。';
        area.appendChild(empty);
      }
      for (const item of rows.slice(0,findingLimit)) {
        const button = document.createElement('button'); button.className='finding';
        const badge = document.createElement('span'); badge.className='severity '+item.severity;
        badge.textContent = {high:'高',medium:'中',low:'低'}[item.severity] || item.severity;
        const content = document.createElement('div');
        const title = document.createElement('div'); title.className='finding-title'; title.textContent=item.title;
        const path = document.createElement('div'); path.className='finding-path'; path.textContent=item.path+' : '+item.line;
        const code = document.createElement('span'); code.className='finding-code'; code.textContent=item.code;
        content.append(title,path); button.append(badge,content,code); button.title=item.suggestion;
        button.onclick=()=>openFile(item.path,item.line); area.appendChild(button);
      }
      if (rows.length > findingLimit) {
        const more = document.createElement('button'); more.className='finding'; more.textContent='显示更多（剩余 '+(rows.length-findingLimit)+' 项）';
        more.onclick=()=>{findingLimit+=60;renderFindings();}; area.appendChild(more);
      }
    }
    async function runScan(changedOnly) {
      const buttons=[$('scan-project'),$('scan-changed')]; buttons.forEach(button=>button.disabled=true);
      const button=changedOnly?buttons[1]:buttons[0]; const original=button.textContent; button.textContent='扫描中…'; notify('');
      try {
        scanResult=await api('/api/project/scan',{changed_only:changedOnly,max_files:100});
        $('metric-score').textContent=scanResult.average_score ?? '—';
        $('metric-high').textContent=scanResult.severity_summary.high;
        $('metric-lines').textContent=scanResult.lines.toLocaleString();
        $('metric-lines-note').textContent=scanResult.files_scanned+' 个文件 · '+scanResult.duration_ms+' ms';
        $('finding-count').textContent=scanResult.findings_total+' 项';
        $('scan-note').textContent=(changedOnly?'Git 改动范围':'整个项目')+' · 扫描 '+scanResult.files_scanned+' 个文件，跳过 '+scanResult.skipped.length+' 个。'+(scanResult.truncated?'结果已截断。':'')+' 点击问题查看源码，悬停查看修复建议。';
        $('export-scan').disabled=false; findingLimit=60; renderFindings();
      } catch(error) { notify(error.message); }
      finally {buttons.forEach(button=>button.disabled=false);button.textContent=original;}
    }
    function saveJSON(value, filename) {
      const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
      const link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    async function loadGit() {
      const button=$('refresh-git');button.disabled=true;button.textContent='读取中…';
      try {
        const data=await api('/api/project/git',{});
        $('git-branch').textContent='⑂ '+data.branch;
        $('git-count').textContent=data.changed_count+' 个改动文件';
        $('git-note').textContent=data.note+(data.truncated?' 结果已截断。':'');
        $('git-files').replaceChildren();
        for(const file of data.files){
          const row=document.createElement('button');row.className='git-file';
          const status=document.createElement('b');status.textContent=file.status;
          const name=document.createElement('span');name.textContent=file.path;
          row.append(status,name);row.onclick=()=>openFile(file.path);$('git-files').appendChild(row);
        }
        $('git-diff').replaceChildren();$('git-diff').hidden=!data.diff;
        for(const text of data.diff.split('\n')){
          const row=document.createElement('span');row.className=text.startsWith('+')?'addition':text.startsWith('-')?'deletion':text.startsWith('@@')?'hunk':'';
          row.textContent=text+'\n';$('git-diff').appendChild(row);
        }
      } catch(error){$('git-note').textContent=error.message;$('git-files').replaceChildren();$('git-diff').hidden=true;$('git-count').textContent='不可用';}
      finally{button.disabled=false;button.textContent='↻ 刷新改动';}
    }
    async function loadSessions() {
      try{
        const data=await api('/api/sessions');const select=$('history-select');
        select.replaceChildren(new Option('恢复历史会话…',''));
        for(const session of data.sessions)select.add(new Option(session.session_id,session.session_id));
      }catch(error){$('history-select').title=error.message;}
    }
    async function restoreSession(name) {
      if(!name || sendBtn.disabled)return;
      try{
        const data=await api('/api/history?session='+encodeURIComponent(name));
        sessionEl.value=data.session;thread.replaceChildren();showView('assistant');
        for(const message of data.messages){
          if(message.role==='user')userBubble(message.content.replace(/^\[mode:[^\]]+\]\n/,''));
          else {const card=assistantCard();card.info.textContent='历史记录';card.answer.innerHTML=renderMarkdown(message.content);}
        }
        if(!data.messages.length)thread.appendChild(emptyBox);
        scrollDown();
      }catch(error){showView('overview');notify('恢复会话失败：'+error.message);}
    }
    document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>showView(button.dataset.view));
    $('refresh-files').onclick=loadProject;$('file-search').oninput=renderFiles;
    $('scan-project').onclick=()=>runScan(false);$('scan-changed').onclick=()=>runScan(true);
    $('severity-filter').onchange=()=>{findingLimit=60;renderFindings();};
    $('export-scan').onclick=()=>saveJSON({...scanResult,created_at:new Date().toISOString()},'project-scan-'+Date.now()+'.json');
    $('refresh-git').onclick=loadGit;
    $('ask-git').onclick=()=>{showView('assistant');modeSel.value='review';input.value='审查 Git 改动，指出潜在回归风险和建议验证的测试';autoGrow();input.focus();};
    $('close-preview').onclick=()=>$('file-dialog').close();
    $('review-file').onclick=()=>askForFile('review');$('explain-file').onclick=()=>askForFile('explain');
    $('history-select').onchange=event=>restoreSession(event.target.value);
    sessionEl.onchange=()=>restoreSession(sessionEl.value);
    $('new-chat').onclick=()=>{if(sendBtn.disabled)return;sessionEl.value='work-'+Date.now();thread.replaceChildren(emptyBox);showView('assistant');input.focus();};
    $('run-project-tests').onclick=async()=>{
      const button=$('run-project-tests');button.disabled=true;button.textContent='运行中…';
      $('test-result').className='test-status';$('test-result').textContent='正在执行项目测试，最多等待 120 秒…';
      try{const result=await api('/api/project/tests',{path:$('test-path').value});
        $('test-result').className='test-output';
        $('test-result').textContent=(result.passed?'进程成功':'测试失败')+' · '+(result.tests_run??'未知')+' 个用例 · '+result.skipped+' 个跳过 · '+result.failure_count+' 个失败/错误\n\n'+result.output;
      }catch(error){$('test-result').textContent=error.message;}
      finally{button.disabled=false;button.textContent='▷ 运行测试';}
    };

    async function loadStatus() {
      const node = document.getElementById('status');
      try {
        const resp = await fetch('/api/status');
        const s = await resp.json();
        providerSel.value = s.provider;
        $('run-project-tests').disabled = !s.allow_exec;
        if (!s.allow_exec) $('test-result').textContent = '当前启动配置禁止执行测试。';
        fileEl.disabled = s.read_only;
        fileEl.dataset.readOnly = String(s.read_only);
        node.textContent = '模型来源 ' + s.provider + ' · 模型 ' + s.model
          + ' · 工具 ' + (s.tools || []).length + ' 个';
        node.title = '工作区：' + s.workspace + '；API Key：' + s.api_key_masked;
      } catch (e) {
        node.textContent = '无法连接服务，请确认 python webui.py 还在运行';
      }
    }

    async function uploadFile(file) {
      if (file.size > 2000000) { alert('文件超过 2MB，无法上传'); return null; }
      let text = '';
      try { text = await file.text(); }
      catch (e) { alert('读取文件失败：' + e); return null; }
      const resp = await fetch('/api/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content: text })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) { alert('上传失败：' + (data.error || ('HTTP ' + resp.status))); return null; }
      return data.path;
    }

    function downloadReport(card, report) {
      for (const format of ['md', 'json']) {
        const button = document.createElement('button');
        button.className = 'chip-btn';
        button.textContent = format === 'md' ? '下载报告' : '下载 JSON 轨迹';
        button.onclick = () => {
          const content = format === 'json' ? JSON.stringify(report, null, 2) : [
            '# 代码助手任务报告', '',
            '- 模型来源：' + report.provider + ' / ' + report.model,
            '- 模式：' + report.mode + '，迭代：' + report.iterations + '，状态：' + report.stopped,
            '', '## 用户任务', '', report.task, '', '## 结果', '', report.answer,
            '', '## 执行轨迹', '',
            ...(report.steps || []).map((step, i) => (i + 1) + '. ' + step.title + '（' + (step.ok ? '成功' : '失败') + '）：' + step.detail),
            '', report.provider === 'mock' ? '> 本报告来自离线规则引擎，用于验证流程，不代表真实大模型能力。' : ''
          ].join('\n');
          const url = URL.createObjectURL(new Blob([content], {type: format === 'json' ? 'application/json' : 'text/markdown;charset=utf-8'}));
          const link = document.createElement('a');
          link.href = url;
          link.download = 'code-agent-' + report.mode + '-' + Date.now() + '.' + format;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
        card.answer.parentNode.appendChild(button);
      }
    }

    async function send() {
      const text = input.value.trim();
      const file = fileEl.files[0];
      if ((!text && !file) || sendBtn.disabled) return;
      sendBtn.disabled = true;
      const controls = [modeSel, providerSel, sessionEl, fileEl, $('history-select'), $('new-chat')];
      controls.forEach(node => { node.disabled = true; });
      let card = null;
      let thinking = null;
      try {
        let message = text;
        if (file) {
          const path = await uploadFile(file);
          if (!path) return;
          fileEl.value = '';
          renderFileChip(null);
          message = text ? (text + ' （文件：' + path + '）')
            : (UPLOAD_PROMPTS[modeSel.value] || UPLOAD_PROMPTS.review).replace('{path}', path);
        }
        hideEmpty();
        userBubble(message);
        input.value = '';
        autoGrow();
        card = assistantCard();
        thinking = thinkingRow(card.steps);
        const response = await fetch('/api/chat/stream', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message, mode: modeSel.value, session: sessionEl.value || 'web', provider: providerSel.value})
        });
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(error.error || ('HTTP ' + response.status));
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let completed = false;
        while (true) {
          const {value, done} = await reader.read();
          buffer += decoder.decode(value, {stream: !done});
          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const dataLine = frame.split('\n').find(line => line.startsWith('data: '));
            if (!dataLine) continue;
            const data = JSON.parse(dataLine.slice(6));
            if (frame.startsWith('event: done')) {
              if (data.kind === 'error') throw new Error(data.detail || '执行失败');
              completed = true;
              card.badge.textContent = MODE_LABEL[data.mode] || data.mode;
              card.info.textContent = data.iterations + ' 轮迭代 · ' + data.stopped;
              card.answer.innerHTML = renderMarkdown(data.answer);
              if (data.stopped === 'error') card.wrap.classList.add('error');
              if (data.report) downloadReport(card, data.report);
            } else if (['tool', 'error', 'note'].includes(data.kind)) {
              stepRow(card.steps, data.title + '：' + data.detail, data.ok);
            }
          }
          if (done) break;
        }
        if (!completed) throw new Error('连接中断，未收到最终结果。请检查服务状态。');
      } catch (error) {
        if (!card) { hideEmpty(); card = assistantCard(); }
        card.answer.textContent = error.message || String(error);
        card.wrap.classList.add('error');
      } finally {
        if (thinking) thinking.remove();
        sendBtn.disabled = false;
        controls.forEach(node => { node.disabled = false; });
        fileEl.disabled = fileEl.dataset.readOnly === 'true';
        loadSessions();
        input.focus();
        scrollDown();
      }
    }

    function autoGrow() {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    }

    input.addEventListener('input', autoGrow);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    sendBtn.addEventListener('click', send);
    fileEl.addEventListener('change', () => renderFileChip(fileEl.files[0]));
    modeSel.addEventListener('change', () => renderFileChip(fileEl.files[0]));
    document.querySelectorAll('.chip-btn').forEach((btn) => {
      btn.addEventListener('click', () => { input.value = btn.dataset.prompt; autoGrow(); send(); });
    });
    loadStatus();
    loadProject();
    loadSessions();
