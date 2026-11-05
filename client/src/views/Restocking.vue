<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <!-- Full loading state is for the first load only; re-fetches keep the page mounted -->
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error && !plan" class="error">{{ error }}</div>
    <div v-else-if="plan && plan.budget_range.max === 0" class="card empty-state">
      {{ t('restocking.noForecasts') }}
    </div>
    <div v-else-if="plan">
      <div class="card">
        <div class="slider-row">
          <label class="slider-label" for="restock-budget">{{ t('restocking.budgetLabel') }}</label>
          <span class="slider-value">{{ formatMoney(budget) }}</span>
          <span v-if="refreshing" class="slider-status">{{ t('restocking.updating') }}</span>
        </div>
        <!-- The model stays in USD (what the server expects); only the labels convert for display -->
        <input
          id="restock-budget"
          class="budget-slider"
          type="range"
          v-model.number="budget"
          :min="plan.budget_range.min"
          :max="plan.budget_range.max"
          :step="plan.budget_range.step"
          :aria-valuetext="formatMoney(budget)"
          :disabled="submitting"
        />
        <div class="slider-range">
          {{ t('restocking.budgetRange', {
            min: formatMoney(plan.budget_range.min),
            max: formatMoney(plan.budget_range.max)
          }) }}
        </div>
      </div>

      <div v-if="error" class="error">{{ error }}</div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.summary.totalCost') }}</div>
          <div class="stat-value">{{ formatMoney(plan.total_cost) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.summary.unspent') }}</div>
          <div class="stat-value">{{ formatMoney(plan.unspent) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.summary.leadTime') }}</div>
          <div class="stat-value">{{ leadTimeText }}</div>
        </div>
        <div :class="['stat-card', plan.fully_funded ? 'success' : 'warning']">
          <div class="stat-label">{{ t('restocking.summary.unfunded') }}</div>
          <div class="stat-value">{{ plan.unfunded_count }}</div>
          <div class="stat-note">
            {{ plan.fully_funded
              ? t('restocking.summary.allFunded')
              : t('restocking.summary.unfundedCount', { count: plan.unfunded_count }) }}
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ plan.items.length }})</h3>
        </div>
        <div v-if="plan.items.length === 0" class="empty-state">{{ t('restocking.noItems') }}</div>
        <div v-else :class="['table-container', { stale: refreshing }]">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th>{{ t('restocking.table.quantity') }}</th>
                <th>{{ t('restocking.table.unitCost') }}</th>
                <th>{{ t('restocking.table.lineTotal') }}</th>
                <th>{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in plan.items" :key="item.sku">
                <td>{{ translateProductName(item.name) }}</td>
                <td><strong>{{ item.sku }}</strong></td>
                <td>
                  <span :class="['badge', item.trend]">
                    {{ t(`trends.${item.trend}`) }}
                  </span>
                </td>
                <td>
                  {{ t('restocking.quantityOf', { quantity: item.quantity, target: item.target_quantity }) }}
                  <span v-if="item.is_partial" class="badge warning partial-marker">
                    {{ t('restocking.partial') }}
                  </span>
                </td>
                <td>{{ formatMoney(item.unit_cost) }}</td>
                <td><strong>{{ formatMoney(item.line_total) }}</strong></td>
                <td>{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="order-actions">
        <button
          type="button"
          class="place-order-btn"
          :disabled="!canPlaceOrder"
          @click="placeOrder"
        >
          {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
        </button>
        <div v-if="orderError" class="error order-message">{{ orderError }}</div>
        <div v-if="placedOrder" class="order-success">
          {{ t('restocking.orderPlaced', { orderNumber: placedOrder.order_number }) }}
          <router-link to="/orders">{{ t('restocking.viewOrders') }}</router-link>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrencyWithDecimals } from '../utils/currency'

const DEBOUNCE_MS = 200

// crypto.randomUUID is unavailable on non-secure origins, so build the id by hand.
// The server requires 8 to 64 characters; this is about 30.
const generateRequestId = () => {
  return `restock-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    const loading = ref(true)
    const refreshing = ref(false)
    const error = ref(null)
    const plan = ref(null)
    // Always USD, exactly what is sent to the server. Never store a converted value here.
    const budget = ref(0)

    const submitting = ref(false)
    const orderError = ref(null)
    const placedOrder = ref(null)
    // Fixed for the current draft so a retry after a network error reuses the same id
    // and the server can de-duplicate; regenerated only when the budget changes.
    const requestId = ref(generateRequestId())

    let debounceTimer = null
    let requestCounter = 0
    let failedAttempt = null

    const formatMoney = (amount) => {
      return formatCurrencyWithDecimals(amount, currentCurrency.value, 2)
    }

    const leadTimeText = computed(() => {
      const days = plan.value && plan.value.lead_time_days
      if (typeof days !== 'number') return t('restocking.summary.noLeadTime')
      return t('restocking.summary.leadTimeDays', { count: days })
    })

    const canPlaceOrder = computed(() => {
      return !loading.value &&
        !submitting.value &&
        !!plan.value &&
        plan.value.items.length > 0 &&
        plan.value.budget === budget.value &&
        !placedOrder.value
    })

    const fetchPlan = async (requestedBudget) => {
      // Stale-response guard: slider moves can overlap requests, and responses may arrive out
      // of order. Only the most recently issued request is allowed to update the page.
      const myRequest = ++requestCounter
      try {
        error.value = null
        const result = await api.getRestockRecommendations(requestedBudget)
        if (myRequest !== requestCounter) return
        plan.value = result
        return result
      } catch (err) {
        if (myRequest !== requestCounter) return
        error.value = t('restocking.loadFailed') + ': ' + err.message
      } finally {
        if (myRequest === requestCounter) refreshing.value = false
      }
    }

    const loadInitial = async () => {
      loading.value = true
      // No budget on first load so the server picks its default
      const result = await fetchPlan(null)
      if (result) {
        budget.value = result.budget_range.default
      }
      loading.value = false
    }

    watch(budget, (newBudget) => {
      if (!plan.value) return
      clearTimeout(debounceTimer)
      // A failed POST may still have reached the server, so coming back to that budget
      // must reuse its id. A new id here could place the same order twice.
      requestId.value = failedAttempt && failedAttempt.budget === newBudget
        ? failedAttempt.requestId
        : generateRequestId()
      placedOrder.value = null
      orderError.value = null

      if (newBudget === plan.value.budget) {
        // Back on the budget the plan already shows: invalidate anything in flight
        requestCounter++
        refreshing.value = false
        error.value = null
        return
      }

      refreshing.value = true
      debounceTimer = setTimeout(() => fetchPlan(newBudget), DEBOUNCE_MS)
    })

    const placeOrder = async () => {
      if (!canPlaceOrder.value) return
      submitting.value = true
      orderError.value = null
      try {
        // Exact body only; the server rejects any extra field with 422
        placedOrder.value = await api.placeRestockOrder({
          budget: budget.value,
          request_id: requestId.value
        })
        failedAttempt = null
      } catch (err) {
        failedAttempt = { budget: budget.value, requestId: requestId.value }
        const detail = err.response && err.response.data && err.response.data.detail
        orderError.value = typeof detail === 'string'
          ? detail
          : t('restocking.orderFailed') + ': ' + err.message
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadInitial)

    onBeforeUnmount(() => {
      clearTimeout(debounceTimer)
      // Discard any response still in flight
      requestCounter++
    })

    return {
      t,
      loading,
      refreshing,
      error,
      plan,
      budget,
      submitting,
      orderError,
      placedOrder,
      leadTimeText,
      canPlaceOrder,
      formatMoney,
      placeOrder,
      translateProductName
    }
  }
}
</script>

<style scoped>
.slider-row {
  display: flex;
  align-items: baseline;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.slider-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.slider-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.slider-status {
  margin-left: auto;
  font-size: 0.813rem;
  color: #64748b;
}

.budget-slider {
  width: 100%;
}

.slider-range {
  margin-top: 0.5rem;
  font-size: 0.813rem;
  color: #64748b;
}

.stat-note {
  margin-top: 0.375rem;
  font-size: 0.813rem;
  color: #64748b;
}

.empty-state {
  text-align: center;
  padding: 1.5rem;
  color: #64748b;
  font-size: 0.938rem;
}

.stale {
  opacity: 0.6;
  transition: opacity 0.15s ease;
}

.partial-marker {
  margin-left: 0.5rem;
}

.order-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1rem;
}

.place-order-btn {
  padding: 0.625rem 1.5rem;
  border: none;
  border-radius: 6px;
  background: #0f172a;
  color: white;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.order-message {
  margin: 0;
}

.order-success {
  color: #065f46;
  font-size: 0.938rem;
  font-weight: 500;
}

.order-success a {
  margin-left: 0.5rem;
  color: #2563eb;
}
</style>
