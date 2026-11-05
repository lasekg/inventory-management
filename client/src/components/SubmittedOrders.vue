<template>
  <div class="card submitted-orders">
    <div class="card-header">
      <h3 class="card-title">{{ t('orders.submitted.title') }} ({{ orders.length }})</h3>
    </div>
    <p class="submitted-note">{{ t('orders.submitted.note') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="orders.length === 0" class="empty-state">{{ t('orders.submitted.empty') }}</div>
    <div v-else class="table-container">
      <table class="submitted-table">
        <thead>
          <tr>
            <th>{{ t('orders.submitted.table.orderNumber') }}</th>
            <th>{{ t('orders.submitted.table.items') }}</th>
            <th>{{ t('orders.submitted.table.status') }}</th>
            <th>{{ t('orders.submitted.table.orderDate') }}</th>
            <th>{{ t('orders.submitted.table.leadTime') }}</th>
            <th>{{ t('orders.submitted.table.expectedDelivery') }}</th>
            <th>{{ t('orders.submitted.table.total') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="order in orders" :key="order.id">
            <td><strong>{{ order.order_number }}</strong></td>
            <td>
              <!-- Expands in normal flow so the table container's overflow can't clip it -->
              <details class="items-details">
                <summary class="items-summary">
                  {{ t('orders.itemsCount', { count: order.items.length }) }}
                </summary>
                <ul class="item-list">
                  <li v-for="item in order.items" :key="item.sku" class="item-entry">
                    <span class="item-name">{{ translateProductName(item.name) }}</span>
                    <span class="item-meta">
                      {{ t('orders.quantity') }}: {{ item.quantity }},
                      {{ t('orders.submitted.unitCost') }}: {{ formatMoney(item.unit_cost) }}
                    </span>
                  </li>
                </ul>
              </details>
            </td>
            <td>
              <span class="badge info">{{ t('status.submitted') }}</span>
            </td>
            <td>{{ formatDate(order.order_date) }}</td>
            <td class="lead-time">{{ formatLeadTime(order.lead_time_days) }}</td>
            <td>{{ formatDate(order.expected_delivery) }}</td>
            <td><strong>{{ formatMoney(order.total_cost) }}</strong></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrencyWithDecimals } from '../utils/currency'

export default {
  name: 'SubmittedOrders',
  setup() {
    const { t, currentLocale, currentCurrency, translateProductName } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const orders = ref([])

    const loadOrders = async () => {
      try {
        loading.value = true
        error.value = null
        orders.value = await api.getSubmittedOrders()
      } catch (err) {
        error.value = t('orders.submitted.loadFailed') + ': ' + err.message
      } finally {
        loading.value = false
      }
    }

    // API amounts are USD; the formatter converts for display only
    const formatMoney = (amount) => {
      return formatCurrencyWithDecimals(amount, currentCurrency.value, 2)
    }

    const formatLeadTime = (days) => {
      if (typeof days !== 'number') return '-'
      return t('orders.submitted.leadTimeDays', { count: days })
    }

    const formatDate = (dateString) => {
      // Validate first: an unparseable date would render "Invalid Date"
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return '-'
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    onMounted(loadOrders)

    return {
      t,
      loading,
      error,
      orders,
      formatMoney,
      formatLeadTime,
      formatDate,
      translateProductName
    }
  }
}
</script>

<style scoped>
.submitted-note {
  color: #64748b;
  font-size: 0.875rem;
  margin-bottom: 1rem;
}

.empty-state {
  text-align: center;
  padding: 1.5rem;
  color: #64748b;
  font-size: 0.938rem;
}

.lead-time {
  font-weight: 600;
  color: #0f172a;
  white-space: nowrap;
}

.items-summary {
  cursor: pointer;
  color: #3b82f6;
  font-weight: 500;
  user-select: none;
}

.items-summary:hover {
  color: #2563eb;
  text-decoration: underline;
}

.item-list {
  list-style: none;
  margin-top: 0.5rem;
  min-width: 240px;
}

.item-entry {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid #f1f5f9;
}

.item-entry:last-child {
  border-bottom: none;
}

.item-name {
  font-size: 0.875rem;
  font-weight: 500;
  color: #0f172a;
}

.item-meta {
  font-size: 0.813rem;
  color: #64748b;
}
</style>
