document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const chatHistory = document.getElementById('chatHistory');
    const userInput = document.getElementById('userInput');
    const sendBtn = document.getElementById('sendBtn');
    const uploadBtn = document.getElementById('uploadBtn');
    const fileInput = document.getElementById('fileInput');

    // Workspace Elements
    const initialState = document.getElementById('initialState');
    const workspaceContent = document.getElementById('workspaceContent');
    const sourceDataContent = document.getElementById('sourceDataContent');
    const voucherRows = document.getElementById('voucherRows');
    const totalDebitEl = document.getElementById('totalDebit');
    const totalCreditEl = document.getElementById('totalCredit');
    const createTransactionBtn = document.getElementById('createTransactionBtn');
    const sourceBadge = document.getElementById('sourceBadge');
    const toggleSourceBtn = document.getElementById('toggleSourceData');

    // State
    let isProcessing = false;
    let sessionId = null;        // Backend session ID
    let pendingFile = null;      // File selected but not yet sent
    let currentVoucherId = null; // Current voucher being displayed
    let rulesLoaded = false;     // Whether voucher rules have been fetched

    // --- Interaction Logic ---

    // Send Message
    async function sendMessage() {
        const text = userInput.value.trim();
        if (!text && !pendingFile) return;
        if (isProcessing) return;

        // User Message in chat
        addMessage(text || '上传了文件', 'user');

        // Clear Input
        userInput.value = '';
        resizeTextarea();

        // Disable input
        isProcessing = true;
        showTypingIndicator();

        try {
            if (pendingFile) {
                await handleFileUpload(pendingFile, text);
                pendingFile = null;
            } else {
                await processAIResponse(text);
            }
        } catch (err) {
            removeTypingIndicator();
            addMessage('网络错误，请重试。', 'ai');
            console.error(err);
        } finally {
            isProcessing = false;
        }
    }

    // Add Message to Chat
    function addMessage(content, type = 'ai') {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${type}-message`;

        let icon = type === 'ai' ? 'ph-fire' : 'ph-user';

        msgDiv.innerHTML = `
            <div class="avatar"><i class="ph ${icon}"></i></div>
            <div class="content">${formatContent(content)}</div>
        `;

        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function formatContent(text) {
        return text.replace(/\n/g, '<br>');
    }

    // Resize Textarea
    function resizeTextarea() {
        userInput.style.height = 'auto';
        userInput.style.height = userInput.scrollHeight + 'px';
    }

    // --- API Calls ---

    async function processAIResponse(input) {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: input,
                session_id: sessionId,
            }),
        });

        const data = await resp.json();

        removeTypingIndicator();
        sessionId = data.session_id;

        addMessage(data.reply, 'ai');

        if (data.voucher) {
            currentVoucherId = data.voucher.voucher_id;
            activateWorkspace(data.voucher);
        }

        // If rules data is returned, switch to rules tab and render filtered rules
        if (data.rules && data.rules.length > 0) {
            rulesLoaded = true;
            switchTab('rules');
            renderRules(data.rules);
        }
    }

    async function handleFileUpload(file, extraMessage) {
        const formData = new FormData();
        formData.append('file', file);
        if (sessionId) formData.append('session_id', sessionId);

        const resp = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
        });

        const data = await resp.json();

        removeTypingIndicator();
        sessionId = data.session_id;

        addMessage(data.reply, 'ai');

        // Show source data
        if (data.file) {
            showSourceData(data.file);
        }

        // Show vouchers
        if (data.vouchers && data.vouchers.length > 0) {
            // Show the last voucher in the workspace
            const lastVoucher = data.vouchers[data.vouchers.length - 1];
            currentVoucherId = lastVoucher.voucher_id;
            activateWorkspace(lastVoucher);

            // If multiple vouchers, mention them in chat
            if (data.vouchers.length > 1) {
                addMessage(`共生成 ${data.vouchers.length} 张凭证，当前显示最后一张。`, 'ai');
            }
        }
    }

    let isPosted = false; // Track if current voucher has been posted

    async function confirmVoucher() {
        if (!currentVoucherId || isPosted) return;

        const btnText = createTransactionBtn.innerHTML;
        createTransactionBtn.innerHTML = `<i class="ph ph-spinner"></i> 过账中...`;
        createTransactionBtn.disabled = true;

        try {
            const resp = await fetch('/api/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: sessionId,
                    voucher_id: currentVoucherId,
                }),
            });

            const data = await resp.json();

            isPosted = true;
            createTransactionBtn.innerHTML = `<i class="ph ph-check"></i> 已记账`;
            createTransactionBtn.style.background = '#059669';
            // Keep disabled — no resetting back

            addMessage(data.message, 'ai');
        } catch (err) {
            createTransactionBtn.innerHTML = btnText;
            createTransactionBtn.style.background = '';
            createTransactionBtn.disabled = false;
            addMessage('过账失败，请重试。', 'ai');
        }
    }

    // --- Workspace Logic ---

    function showSourceData(fileInfo) {
        const name = fileInfo.name || '';
        const ext = name.split('.').pop().toLowerCase();
        const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];
        const pdfExts = ['pdf'];
        let iconClass = 'ph-file-xls';
        if (imageExts.includes(ext)) iconClass = 'ph-image';
        else if (pdfExts.includes(ext)) iconClass = 'ph-file-pdf';

        sourceDataContent.innerHTML = `
            <div class="source-item">
                <i class="ph ${iconClass} icon"></i>
                <div class="details">
                    <div class="name">${name}</div>
                    <div class="meta">${fileInfo.size_kb} KB • 刚刚上传</div>
                </div>
            </div>
        `;
        sourceBadge.textContent = '1 份文件';
    }

    function activateWorkspace(voucherData) {
        // If first time, hide empty state and show content
        if (initialState.style.display !== 'none') {
            initialState.style.display = 'none';
            workspaceContent.style.display = 'block';
        }

        // Reset post state for new voucher
        isPosted = false;
        createTransactionBtn.innerHTML = '确认并记账 <i class="ph ph-check-circle"></i>';
        createTransactionBtn.style.background = '';
        createTransactionBtn.disabled = false;

        // Update header info
        document.getElementById('voucherIdField').textContent = voucherData.voucher_id || '—';
        document.getElementById('companyCodeField').textContent = voucherData.company_code || '—';
        document.getElementById('docTypeField').textContent = voucherData.document_type || '—';
        document.getElementById('referenceField').textContent = voucherData.reference || '—';
        document.getElementById('headerTextField').textContent = voucherData.header_text || '—';
        document.getElementById('confidenceField').textContent = voucherData.confidence || '—';

        if (voucherData.document_date) {
            document.getElementById('docDateField').value = voucherData.document_date;
        }
        if (voucherData.posting_date) {
            document.getElementById('postDateField').value = voucherData.posting_date;
        }

        // Warnings
        const warningsBar = document.getElementById('warningsBar');
        if (voucherData.warnings && voucherData.warnings.length > 0) {
            warningsBar.innerHTML = '⚠️ ' + voucherData.warnings.join('；');
            warningsBar.style.display = 'block';
        } else {
            warningsBar.style.display = 'none';
        }

        renderVoucher(voucherData.rows);
    }

    function renderVoucher(rows) {
        voucherRows.innerHTML = '';
        let totalDr = 0;
        let totalCr = 0;

        rows.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td class="col-no">${row.line_no}</td>
                <td class="col-acct"><input type="text" value="${row.account_code}"></td>
                <td class="col-name"><input type="text" value="${row.account_name}"></td>
                <td class="col-dc"><span class="dc-badge ${row.debit_credit === 'S' ? 'dc-debit' : 'dc-credit'}">${row.debit_credit === 'S' ? '借' : '贷'}</span></td>
                <td class="col-amt"><input type="number" value="${row.debit}" class="amount-input" step="0.01"></td>
                <td class="col-amt"><input type="number" value="${row.credit}" class="amount-input" step="0.01"></td>
                <td class="col-cur">${row.currency}</td>
                <td class="col-code"><input type="text" value="${row.customer_code}"></td>
                <td class="col-name"><input type="text" value="${row.customer_name}"></td>
                <td class="col-code"><input type="text" value="${row.tax_code}"></td>
                <td class="col-code"><input type="text" value="${row.profit_center}"></td>
                <td class="col-code"><input type="text" value="${row.cost_center}"></td>
                <td class="col-code"><input type="text" value="${row.assignment}"></td>
                <td class="col-text"><input type="text" value="${row.text}"></td>
                <td><button class="icon-btn-small delete-row-btn" title="删除"><i class="ph ph-trash"></i></button></td>
            `;
            // Bind delete
            tr.querySelector('.delete-row-btn').addEventListener('click', () => {
                tr.remove();
                recalcTotals();
            });

            // Bind amount change
            tr.querySelectorAll('.amount-input').forEach(inp => {
                inp.addEventListener('input', recalcTotals);
            });

            voucherRows.appendChild(tr);
            totalDr += row.debit;
            totalCr += row.credit;
        });

        updateTotals(totalDr, totalCr);
    }

    function recalcTotals() {
        let totalDr = 0;
        let totalCr = 0;
        voucherRows.querySelectorAll('tr').forEach(tr => {
            const inputs = tr.querySelectorAll('.amount-input');
            if (inputs.length >= 2) {
                const dr = parseFloat(inputs[0].value) || 0;
                const cr = parseFloat(inputs[1].value) || 0;
                totalDr += dr;
                totalCr += cr;
            }
        });
        updateTotals(totalDr, totalCr);
    }

    function updateTotals(dr, cr) {
        totalDebitEl.textContent = dr.toFixed(2);
        totalCreditEl.textContent = cr.toFixed(2);
    }

    // --- Typing Indicator ---

    function showTypingIndicator() {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ai-message`;
        msgDiv.id = 'typing-indicator';
        msgDiv.innerHTML = `
            <div class="avatar"><i class="ph ph-fire"></i></div>
            <div class="content">
                <div class="typing-dots">
                    <span>.</span><span>.</span><span>.</span>
                </div>
            </div>
        `;
        chatHistory.appendChild(msgDiv);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    function removeTypingIndicator() {
        const el = document.getElementById('typing-indicator');
        if (el) el.remove();
    }

    // --- Event Listeners ---

    sendBtn.addEventListener('click', sendMessage);

    userInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    userInput.addEventListener('input', resizeTextarea);

    uploadBtn.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            pendingFile = e.target.files[0];
            // Show file name in input area
            userInput.value = `📎 ${pendingFile.name}`;
            resizeTextarea();
        }
    });

    // --- Drag & Drop ---
    const chatPanel = document.querySelector('.sidebar');

    chatPanel.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        chatPanel.classList.add('drag-over');
    });

    chatPanel.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        // Only remove if leaving the sidebar itself (not entering a child)
        if (!chatPanel.contains(e.relatedTarget)) {
            chatPanel.classList.remove('drag-over');
        }
    });

    chatPanel.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        chatPanel.classList.remove('drag-over');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            pendingFile = files[0];
            userInput.value = `📎 ${pendingFile.name}`;
            resizeTextarea();
            // Auto-send on drop
            sendMessage();
        }
    });

    createTransactionBtn.addEventListener('click', confirmVoucher);

    // --- Workspace Tabs ---
    const tabBtns = document.querySelectorAll('.workspace-tab');
    const tabContents = document.querySelectorAll('.tab-content');

    function switchTab(tabName) {
        tabBtns.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });
        tabContents.forEach(content => {
            content.classList.toggle('active', content.id === `tabContent${tabName.charAt(0).toUpperCase() + tabName.slice(1)}`);
        });

        // Lazy-load rules when switching to rules tab
        if (tabName === 'rules' && !rulesLoaded) {
            showRulesGuide();
        }
    }

    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // --- Voucher Rules ---

    function showRulesGuide() {
        const list = document.getElementById('rulesList');
        const loading = document.getElementById('rulesLoading');
        const empty = document.getElementById('rulesEmpty');
        if (loading) loading.style.display = 'none';
        list.innerHTML = '';
        list.style.display = 'none';

        // Show a guide message
        if (empty) {
            empty.innerHTML = `
                <i class="ph ph-book-open"></i>
                <p>请在聊天框中询问凭证规则，例如：</p>
                <ul style="text-align: left; margin: 12px 0; padding-left: 24px;">
                    <li>「凭证规则是什么」— 查看可用的规则类型</li>
                    <li>「我想看销售收入的凭证规则」— 查看具体规则</li>
                </ul>
            `;
            empty.style.display = 'block';
        }
    }

    async function loadVoucherRules() {
        const loading = document.getElementById('rulesLoading');
        const empty = document.getElementById('rulesEmpty');
        const list = document.getElementById('rulesList');

        loading.style.display = 'block';
        empty.style.display = 'none';
        list.style.display = 'none';

        try {
            const resp = await fetch('/api/rules');
            const data = await resp.json();

            loading.style.display = 'none';

            if (!data.rules || data.rules.length === 0) {
                empty.style.display = 'block';
                return;
            }

            rulesLoaded = true;
            list.style.display = 'flex';
            renderRules(data.rules);
        } catch (err) {
            loading.style.display = 'none';
            empty.innerHTML = '<i class="ph ph-warning-circle"></i> 加载凭证规则失败，请刷新重试';
            empty.style.display = 'block';
        }
    }

    function renderRules(rules) {
        const list = document.getElementById('rulesList');
        const loading = document.getElementById('rulesLoading');
        const empty = document.getElementById('rulesEmpty');
        list.innerHTML = '';
        if (loading) loading.style.display = 'none';
        if (empty) empty.style.display = 'none';
        list.style.display = 'flex';

        const bizTypeLabels = {
            'sales_revenue': '销售收入',
            'expense': '费用报销',
            'asset_purchase': '资产采购',
            'salary': '工资薪酬',
            'loan': '借款/还款',
        };

        rules.forEach(rule => {
            const bizLabel = bizTypeLabels[rule.business_type] || rule.business_type;
            const productLabel = rule.product_type === '*' ? '全部' : rule.product_type;
            const taxLabel = rule.tax_rate === '*' ? '全部' : rule.tax_rate;
            const docLabel = rule.document_type || 'DR';

            let linesHTML = '';
            rule.lines.forEach(line => {
                const dcClass = line.debit_credit === 'S' ? 'rule-dc-debit' : 'rule-dc-credit';
                linesHTML += `
                    <tr>
                        <td>${line.line_no}</td>
                        <td><span class="rule-dc-badge ${dcClass}">${line.debit_credit_display}</span></td>
                        <td>${line.account_code}</td>
                        <td>${line.account_name}</td>
                        <td><span class="rule-amount-field">${line.amount_field_display}</span></td>
                        <td>${line.customer_source || '—'}</td>
                        <td>${line.tax_code_rule || '—'}</td>
                        <td>${line.profit_center_source || '—'}</td>
                        <td>${line.cost_center_source || '—'}</td>
                        <td>${line.assignment_source || '—'}</td>
                        <td><span class="rule-text-template">${line.text_template || '—'}</span></td>
                    </tr>
                `;
            });

            const card = document.createElement('div');
            card.className = 'rule-card';
            card.innerHTML = `
                <div class="rule-card-header">
                    <div class="rule-card-title">
                        <span class="rule-code-badge">${rule.rule_code}</span>
                        <h3>${bizLabel}</h3>
                    </div>
                    <div class="rule-card-meta">
                        <span class="meta-tag"><i class="ph ph-package"></i> 产品: ${productLabel}</span>
                        <span class="meta-tag"><i class="ph ph-percent"></i> 税率: ${taxLabel}</span>
                        <span class="meta-tag"><i class="ph ph-file-text"></i> 凭证类型: ${docLabel}</span>
                    </div>
                </div>
                <table class="rule-lines-table">
                    <thead>
                        <tr>
                            <th>行号</th>
                            <th>借/贷</th>
                            <th>科目代码</th>
                            <th>科目名称</th>
                            <th>金额取值</th>
                            <th>客户来源</th>
                            <th>税码规则</th>
                            <th>利润中心</th>
                            <th>成本中心</th>
                            <th>分配</th>
                            <th>摘要模板</th>
                        </tr>
                    </thead>
                    <tbody>${linesHTML}</tbody>
                </table>
            `;
            list.appendChild(card);
        });
    }

    // Toggle source data section
    if (toggleSourceBtn) {
        toggleSourceBtn.addEventListener('click', () => {
            const isVisible = sourceDataContent.style.display !== 'none';
            sourceDataContent.style.display = isVisible ? 'none' : 'block';
            toggleSourceBtn.innerHTML = isVisible
                ? '<i class="ph ph-caret-down"></i>'
                : '<i class="ph ph-caret-up"></i>';
        });
    }
});
