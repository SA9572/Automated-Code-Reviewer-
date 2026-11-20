const API_BASE = 'http://localhost:8001';

let currentReviewId = null;
let reviewsPage = 0;
const reviewsLimit = 10;

async function apiRequest(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const config = {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers,
        },
        ...options,
    };

    if (config.body && typeof config.body === 'object') {
        config.body = JSON.stringify(config.body);
    }

    const response = await fetch(url, config);

    if (!response.ok) {
        let errorDetail = `Request failed with status ${response.status}`;
        try {
            const errorBody = await response.json();
            errorDetail = errorBody.detail || errorDetail;
        } catch (_) {
            // Ignore JSON parse errors for error bodies
        }
        throw new Error(errorDetail);
    }

    if (response.status === 204) {
        return null;
    }

    return response.json();
}

function showSection(sectionId) {
    document.querySelectorAll('.section').forEach((section) => {
        section.classList.remove('active');
    });
    document.querySelectorAll('.nav-btn').forEach((btn) => {
        btn.classList.remove('active');
    });

    const section = document.getElementById(sectionId);
    const navBtn = document.getElementById(`nav-${sectionId}`);

    if (section) {
        section.classList.add('active');
    }
    if (navBtn) {
        navBtn.classList.add('active');
    }

    currentReviewId = null;
    document.getElementById('review-detail').classList.add('hidden');
}

function showResult(elementId, message, isError = false) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = message;
    el.className = `result-area ${isError ? 'error' : 'success'} show`;
    setTimeout(() => {
        el.classList.remove('show');
    }, 8000);
}

async function loadHealth() {
    try {
        const health = await apiRequest('/api/v1/health');
        const statusDot = document.getElementById('health-status-dot');
        const statusText = document.getElementById('health-text');
        const dashboardHealth = document.getElementById('dashboard-health');

        if (statusDot) statusDot.className = 'status-dot healthy';
        if (statusText) statusText.textContent = 'Backend is healthy';
        if (dashboardHealth) dashboardHealth.textContent = 'Healthy';
        if (dashboardHealth) dashboardHealth.style.color = '#16a34a';
    } catch (error) {
        const statusDot = document.getElementById('health-status-dot');
        const statusText = document.getElementById('health-text');
        const dashboardHealth = document.getElementById('dashboard-health');

        if (statusDot) statusDot.className = 'status-dot unhealthy';
        if (statusText) statusText.textContent = 'Backend unreachable';
        if (dashboardHealth) dashboardHealth.textContent = 'Unreachable';
        if (dashboardHealth) dashboardHealth.style.color = '#dc2626';
        console.error('Health check failed:', error);
    }
}

async function loadDashboardStats() {
    try {
        const reviews = await apiRequest('/api/v1/reviews?limit=100');
        const total = reviews.total || 0;
        const completed = reviews.items.filter((r) => r.status === 'COMPLETED').length;
        const pending = reviews.items.filter(
            (r) => r.status === 'PENDING' || r.status === 'RUNNING'
        ).length;

        document.getElementById('dashboard-total').textContent = total;
        document.getElementById('dashboard-completed').textContent = completed;
        document.getElementById('dashboard-pending').textContent = pending;
    } catch (error) {
        console.error('Failed to load dashboard stats:', error);
    }
}

async function loadReviews() {
    const search = document.getElementById('review-search').value;
    const status = document.getElementById('review-status-filter').value;

    let url = `/api/v1/reviews?skip=${reviewsPage * reviewsLimit}&limit=${reviewsLimit}`;
    if (search) url += `&repository=${encodeURIComponent(search)}`;
    if (status) url += `&status=${status}`;

    try {
        const response = await apiRequest(url);
        const tbody = document.getElementById('reviews-table-body');
        tbody.innerHTML = '';

        response.items.forEach((review) => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td><code>${review.review_id}</code></td>
                <td>${review.repository}</td>
                <td><span class="status-badge status-${review.status}">${review.status}</span></td>
                <td>${review.total_findings || 0}</td>
                <td>${review.recommendation}</td>
                <td>${new Date(review.created_at).toLocaleString()}</td>
                <td>
                    <button class="btn btn-secondary" onclick="viewReview('${review.review_id}')">View</button>
                </td>
            `;
            tbody.appendChild(row);
        });

        document.getElementById('reviews-pagination').innerHTML = `
            <button class="btn btn-secondary" ${reviewsPage === 0 ? 'disabled' : ''} onclick="changeReviewsPage(-1)">Previous</button>
            <span>Page ${reviewsPage + 1}</span>
            <button class="btn btn-secondary" ${response.skip + response.items.length >= response.total ? 'disabled' : ''} onclick="changeReviewsPage(1)">Next</button>
        `;
    } catch (error) {
        console.error('Failed to load reviews:', error);
        alert('Failed to load reviews: ' + error.message);
    }
}

function changeReviewsPage(delta) {
    reviewsPage += delta;
    loadReviews();
}

async function viewReview(reviewId) {
    try {
        const review = await apiRequest(`/api/v1/reviews/${reviewId}`);
        currentReviewId = reviewId;

        const content = document.getElementById('review-detail-content');
        const findings = review.findings || [];
        const agentResults = review.agent_results || {};

        let agentHtml = '';
        for (const [agentName, result] of Object.entries(agentResults)) {
            agentHtml += `
                <div class="agent-result">
                    <h4>${agentName}</h4>
                    <p><strong>Status:</strong> ${result.status}</p>
                    <p><strong>Summary:</strong> ${result.summary}</p>
                    <p><strong>Findings:</strong> ${result.finding_count || 0}</p>
                    <p><strong>Execution time:</strong> ${result.execution_time_ms} ms</p>
                    ${result.error ? `<p><strong>Error:</strong> ${result.error}</p>` : ''}
                </div>
            `;
        }

        let findingsHtml = '';
        findings.forEach((finding) => {
            findingsHtml += `
                <div class="finding-item">
                    <h4>${finding.title}</h4>
                    <p><strong>Agent:</strong> ${finding.agent_name}</p>
                    <p><strong>Severity:</strong> ${finding.severity}</p>
                    <p><strong>Confidence:</strong> ${(finding.confidence * 100).toFixed(0)}%</p>
                    <p><strong>File:</strong> ${finding.file_path}:${finding.start_line ?? '?'}-${finding.end_line ?? '?'}</p>
                    <p><strong>Description:</strong> ${finding.description}</p>
                    <p><strong>Evidence:</strong> ${finding.evidence}</p>
                    <p><strong>Recommendation:</strong> ${finding.recommendation}</p>
                    ${finding.rule_id ? `<p><strong>Rule:</strong> ${finding.rule_id}</p>` : ''}
                </div>
            `;
        });

        content.innerHTML = `
            <div class="review-detail-card">
                <h4>Review ID</h4>
                <p><code>${review.review_id}</code></p>
                <h4>Repository</h4>
                <p>${review.repository}</p>
                <h4>Status</h4>
                <p><span class="status-badge status-${review.status}">${review.status}</span></p>
                <h4>Recommendation</h4>
                <p>${review.recommendation}</p>
                <h4>Summary</h4>
                <p>${review.summary}</p>
                <h4>Total Findings</h4>
                <p>${review.total_findings || 0}</p>
                <h4>Created</h4>
                <p>${new Date(review.created_at).toLocaleString()}</p>
                ${review.completed_at ? `<h4>Completed</h4><p>${new Date(review.completed_at).toLocaleString()}</p>` : ''}
            </div>

            <div class="agent-results">
                <h3>Agent Results</h3>
                ${agentHtml || '<p>No agent results available.</p>'}
            </div>

            <div class="findings-list">
                <h3>Findings (${findings.length})</h3>
                ${findingsHtml || '<p>No findings.</p>'}
            </div>
        `;

        document.getElementById('review-detail').classList.remove('hidden');
        document.getElementById('review-detail').classList.add('active');
        document.querySelectorAll('.section').forEach((s) => {
            if (s.id !== 'review-detail') s.classList.remove('active');
        });
    } catch (error) {
        alert('Failed to load review: ' + error.message);
    }
}

document.getElementById('create-review-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const changedFilesInput = document.getElementById('changed-files-input').value;
    const testFilesInput = document.getElementById('test-files-input').value;

    const payload = {
        repository: document.getElementById('repo-input').value,
        base_commit: document.getElementById('base-commit-input').value,
        head_commit: document.getElementById('head-commit-input').value,
        diff_content: document.getElementById('diff-content-input').value,
        changed_files: changedFilesInput ? changedFilesInput.split(',').map((s) => s.trim()).filter(Boolean) : [],
        pull_request_number: document.getElementById('pr-number-input').value ? parseInt(document.getElementById('pr-number-input').value, 10) : null,
        test_diff: document.getElementById('test-diff-input').value,
        test_files: testFilesInput ? testFilesInput.split(',').map((s) => s.trim()).filter(Boolean) : [],
    };

    try {
        const result = await apiRequest('/api/v1/reviews', {
            method: 'POST',
            body: payload,
        });
        showResult('create-review-result', `Review created: ${result.review_id}`, false);
        document.getElementById('create-review-form').reset();
        setTimeout(() => viewReview(result.review_id), 500);
    } catch (error) {
        showResult('create-review-result', error.message, true);
    }
});

async function checkGitHubAuth() {
    try {
        const status = await apiRequest('/api/v1/github/auth/status');
        document.getElementById('github-auth-status').textContent = status.configured
            ? 'GitHub is configured'
            : 'GitHub is not configured';
        document.getElementById('github-auth-status').style.color = status.configured ? '#16a34a' : '#dc2626';
    } catch (error) {
        document.getElementById('github-auth-status').textContent = 'Error checking status';
        console.error(error);
    }
}

async function listGitHubRepos() {
    try {
        const repos = await apiRequest('/api/v1/github/repos');
        const listEl = document.getElementById('github-repos-list');
        listEl.innerHTML = '<h3>Your Repositories</h3><ul>' +
            repos.map((repo) => `<li>${repo.full_name} (${repo.private ? 'private' : 'public'})</li>`).join('') +
            '</ul>';
        listEl.classList.add('show');
    } catch (error) {
        alert('Failed to list repositories: ' + error.message);
    }
}

async function listGitHubPRs() {
    const owner = document.getElementById('gh-owner-input').value;
    const repo = document.getElementById('gh-repo-input').value;

    if (!owner || !repo) {
        alert('Please enter owner and repository');
        return;
    }

    try {
        const prs = await apiRequest(`/api/v1/github/repos/${owner}/${repo}/pulls`);
        const listEl = document.getElementById('github-prs-list');
        listEl.innerHTML = '<h3>Pull Requests</h3><ul>' +
            prs.map((pr) => `<li>#${pr.number} - ${pr.title} (${pr.state}) - Head: ${pr.head_sha}</li>`).join('') +
            '</ul>';
        listEl.classList.add('show');
    } catch (error) {
        alert('Failed to list pull requests: ' + error.message);
    }
}

document.getElementById('gh-pr-review-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const owner = document.getElementById('gh-review-owner').value;
    const repo = document.getElementById('gh-review-repo').value;
    const prNumber = document.getElementById('gh-review-pr').value;

    try {
        const result = await apiRequest(
            `/api/v1/github/repos/${owner}/${repo}/pulls/${prNumber}/review`,
            { method: 'POST' }
        );
        showResult('gh-pr-review-result', `Review completed: ${result.review_id}`, false);
        setTimeout(() => viewReview(result.review_id), 500);
    } catch (error) {
        showResult('gh-pr-review-result', error.message, true);
    }
});

document.getElementById('gh-baseline-form').addEventListener('submit', async (e) => {
    e.preventDefault();

    const owner = document.getElementById('gh-baseline-owner').value;
    const repo = document.getElementById('gh-baseline-repo').value;

    try {
        const result = await apiRequest(
            `/api/v1/github/repos/${owner}/${repo}/baseline-review`,
            { method: 'POST' }
        );
        showResult('gh-baseline-result', `Baseline review completed: ${result.review_id}`, false);
        setTimeout(() => viewReview(result.review_id), 500);
    } catch (error) {
        showResult('gh-baseline-result', error.message, true);
    }
});

window.addEventListener('DOMContentLoaded', () => {
    loadHealth();
    loadDashboardStats();
    setInterval(loadHealth, 30000);
});