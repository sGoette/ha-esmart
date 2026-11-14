'use strict'
// Same assembly as @xmpp/client, but SASL PLAIN only.
// The eSmart ejabberd advertises SCRAM-SHA-1 but answers xmpp.js' SCRAM response with <bad-protocol/>.
// PLAIN is safe here because STARTTLS is required by the server (and verified against myesmart.net).

const { xml, jid, Client } = require('@xmpp/client-core')
const _reconnect = require('@xmpp/reconnect')
const _tcp = require('@xmpp/tcp')
const _middleware = require('@xmpp/middleware')
const _streamFeatures = require('@xmpp/stream-features')
const _iqCaller = require('@xmpp/iq/caller')
const _iqCallee = require('@xmpp/iq/callee')
const _starttls = require('@xmpp/starttls/client')
const _sasl = require('@xmpp/sasl')
const _resourceBinding = require('@xmpp/resource-binding')
const _sessionEstablishment = require('@xmpp/session-establishment')
const plain = require('@xmpp/sasl-plain')

function client({ resource, username, password, ...params }) {
  const entity = new Client(params)
  const reconnect = _reconnect({ entity })
  const tcp = _tcp({ entity })
  const middleware = _middleware({ entity })
  const streamFeatures = _streamFeatures({ middleware })
  const iqCaller = _iqCaller({ middleware, entity })
  const iqCallee = _iqCallee({ middleware, entity })
  const starttls = _starttls({ streamFeatures })
  const sasl = _sasl({ streamFeatures }, { username, password })
  const resourceBinding = _resourceBinding({ iqCaller, streamFeatures }, resource)
  const sessionEstablishment = _sessionEstablishment({ iqCaller, streamFeatures })
  plain(sasl)
  return Object.assign(entity, { entity, reconnect, tcp, middleware, streamFeatures, iqCaller, iqCallee, starttls, sasl, resourceBinding, sessionEstablishment })
}

module.exports = { client, xml, jid }
